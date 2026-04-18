"""
Substack Assistant — a multi-agent system that drafts Notes, replies to
comments, and mines your archive in YOUR writing voice.

One-time setup:
    python substack_assist.py setup --feed https://yourname.substack.com/feed

Daily use:
    python substack_assist.py reply --text "<paste comment or note>"
    python substack_assist.py note --topic "your topic"
    python substack_assist.py daily-notes

Saves ~8 hours a week of Substack engagement work.
"""

import os
import json
import argparse
import random
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import feedparser
from anthropic import Anthropic
from html import unescape
import re

client = Anthropic()
MODEL = "claude-opus-4-7"
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)


# ============================================================
# Utilities
# ============================================================
def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1].lstrip("json").strip()
    return text


def _save(name: str, obj) -> None:
    (DATA_DIR / name).write_text(json.dumps(obj, indent=2))


def _load(name: str):
    path = DATA_DIR / name
    if not path.exists():
        raise SystemExit(f"Missing {path}. Run `setup` first.")
    return json.loads(path.read_text())


# ============================================================
# RSS fetcher
# ============================================================
import ssl
import urllib.request
import certifi

def fetch_articles(feed_url: str, limit: int = 30) -> list[dict]:
    # Substack blocks default bot user-agents — send a real browser UA.
    # Also use certifi's CA bundle so macOS + python.org installs work
    # without needing the "Install Certificates.command" step.
    req = urllib.request.Request(
        feed_url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        },
    )
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    try:
        with urllib.request.urlopen(req, timeout=30, context=ssl_ctx) as resp:
            raw_xml = resp.read()
    except Exception as e:
        raise SystemExit(
            f"❌ Couldn't fetch {feed_url}\n   {type(e).__name__}: {e}\n"
            f"   Check the URL opens in your browser."
        )

    feed = feedparser.parse(raw_xml)
    if not feed.entries:
        raise SystemExit(
            f"❌ Feed parsed but contained 0 entries.\n"
            f"   Feed title: {feed.feed.get('title', '(none)')}\n"
            f"   Possible causes:\n"
            f"     • The publication has no public posts yet\n"
            f"     • The feed URL is wrong (try opening it in your browser)\n"
            f"     • Posts are all paid-subscriber-only (RSS only shows free posts)"
        )

    articles = []
    for entry in feed.entries[:limit]:
        raw = entry.get("content", [{}])[0].get("value", entry.get("summary", ""))
        text = unescape(re.sub(r"<[^>]+>", " ", raw))
        text = re.sub(r"\s+", " ", text).strip()
        articles.append({
            "title": entry.title,
            "url": entry.link,
            "published": entry.get("published", ""),
            "text": text,
        })
    return articles


# ============================================================
# AGENT 1: VOICE DISTILLER
# ============================================================
VOICE_SYSTEM = """You are a Voice Analyst studying a single writer's style.
Read the provided article samples and produce a JSON profile:

{
  "tone":                "2-3 sentences on overall tone",
  "sentence_rhythm":     "how sentences flow, length patterns",
  "punctuation_quirks":  ["em-dashes for X", "..."],
  "rhetorical_moves":    ["opens with anecdote", "..."],
  "recurring_metaphors": ["..."],
  "vocabulary_notes":    ["uses X, never uses Y", "..."],
  "what_they_avoid":     ["never corporate-speak", "..."],
  "signature_openings":  ["example opening 1", "example opening 2"],
  "signature_closings":  ["example closing 1", "example closing 2"],
  "stances":             ["holds view X on topic Y", "..."],
  "exemplar_paragraphs": ["3-4 paragraph-length quotes that capture the voice"]
}

Be specific. "Conversational" is useless. "Uses em-dashes to insert
self-aware asides, often undercutting their own seriousness" is useful.
Return ONLY valid JSON."""


def distill_voice(articles: list[dict]) -> dict:
    samples = "\n\n---\n\n".join(
        f"TITLE: {a['title']}\n\n{a['text'][:3000]}" for a in articles[:15]
    )
    resp = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=VOICE_SYSTEM,
        messages=[{"role": "user", "content": samples}],
    )
    return json.loads(_strip_fences(resp.content[0].text))


# ============================================================
# AGENT 2: CONTENT INDEXER
# ============================================================
INDEX_SYSTEM = """You are a Content Indexer. For the given article, return JSON:
{
  "one_liner":      "what this article is really about, in 15 words",
  "topics":         ["3-7 topic tags"],
  "key_claims":     ["2-4 main arguments"],
  "who_it_helps":   "the type of reader who benefits",
  "quotable_lines": ["2-3 short, sharp, quotable lines"]
}
Return ONLY valid JSON."""


def index_article(article: dict) -> dict:
    resp = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=INDEX_SYSTEM,
        messages=[{"role": "user",
                   "content": f"TITLE: {article['title']}\n\n{article['text'][:6000]}"}],
    )
    entry = json.loads(_strip_fences(resp.content[0].text))
    entry.update({"title": article["title"], "url": article["url"]})
    return entry


def build_content_index(articles: list[dict]) -> list[dict]:
    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(index_article, articles))


# ============================================================
# AGENT 3: CONTEXT
# ============================================================
CONTEXT_SYSTEM = """You are a Context Agent. Given an incoming comment or
Substack Note, identify in 3-5 sentences: (1) what the person is really saying,
(2) the emotional register (curious, challenging, warm, skeptical),
(3) whether they're asking a question or making a point."""


# ============================================================
# AGENT 4: RELEVANCE
# ============================================================
RELEVANCE_SYSTEM = """You are a Relevance Agent. Given an incoming text and
the author's content index, return up to 2 entries that are genuinely relevant.
If nothing fits well, return []. Output: JSON array of entries as given."""


# ============================================================
# AGENT 5: DRAFTER
# ============================================================
DRAFTER_SYSTEM = """You are the Drafter. Write 3 short reply drafts. Rules:

- Sound EXACTLY like the voice profile provided. Imitate sentence rhythm,
  punctuation habits, and rhetorical moves.
- If a relevant article is provided, weave ONE natural reference + URL
  into AT MOST one of the three drafts.
- Keep each draft under 80 words.
- Make the 3 drafts genuinely different in angle (one warm, one that adds
  a sharp thought, one that asks back).

Return JSON: {"drafts": ["...", "...", "..."]}"""


# ============================================================
# AGENT 6: VOICE CHECK
# ============================================================
VOICE_CHECK_SYSTEM = """You are a Voice Critic. Score each draft 0-10 on how
well it matches the voice profile. Penalize generic phrases, LinkedIn-speak,
over-eager enthusiasm, and anything the profile says the author avoids.
Return JSON: {"scores": [n, n, n], "notes": "..."}"""


# ============================================================
# AGENT 7: NOTE GENERATOR (archive → ONE standalone note)
# ============================================================
NOTE_GEN_SYSTEM = """You are a Substack Note writer. Write ONE single Note
that works as a standalone observation, then invites the reader to the full
article. Strict rules:

- Sound EXACTLY like the voice profile provided. Imitate sentence rhythm,
  punctuation habits, and rhetorical moves.
- The Note body: 40-80 words. Sharp, standalone, quotable.
- End with the article URL on its own line.
- Return ONLY valid JSON, no preamble, no markdown fences:
  {"note": "<the note text including the URL on a new line>"}
- Do NOT return an array. Do NOT return multiple drafts. ONE note only."""


# ============================================================
# PIPELINES
# ============================================================
def reply(incoming_text: str) -> list[dict]:
    """Generate 3 voice-matched reply drafts, ranked."""
    voice_profile = _load("voice_profile.json")
    content_index = _load("content_index.json")

    # 1. Context
    ctx = client.messages.create(
        model=MODEL, max_tokens=512, system=CONTEXT_SYSTEM,
        messages=[{"role": "user", "content": incoming_text}],
    ).content[0].text

    # 2. Relevance
    rel_raw = client.messages.create(
        model=MODEL, max_tokens=1024, system=RELEVANCE_SYSTEM,
        messages=[{"role": "user", "content":
            f"INCOMING: {incoming_text}\n\nINDEX:\n{json.dumps(content_index)}"}],
    ).content[0].text
    related = json.loads(_strip_fences(rel_raw))

    # 3. Draft
    draft_input = (
        f"VOICE PROFILE:\n{json.dumps(voice_profile, indent=2)}\n\n"
        f"CONTEXT NOTES:\n{ctx}\n\n"
        f"INCOMING TEXT:\n{incoming_text}\n\n"
        f"RELEVANT ARTICLES:\n{json.dumps(related)}"
    )
    drafts = json.loads(_strip_fences(client.messages.create(
        model=MODEL, max_tokens=2048, system=DRAFTER_SYSTEM,
        messages=[{"role": "user", "content": draft_input}],
    ).content[0].text))["drafts"]

    # 4. Voice check
    scores = json.loads(_strip_fences(client.messages.create(
        model=MODEL, max_tokens=1024, system=VOICE_CHECK_SYSTEM,
        messages=[{"role": "user", "content":
            f"PROFILE:\n{json.dumps(voice_profile)}\n\nDRAFTS:\n{json.dumps(drafts)}"}],
    ).content[0].text))

    ranked = sorted(zip(drafts, scores["scores"]),
                    key=lambda x: x[1], reverse=True)
    return [{"draft": d, "voice_score": s} for d, s in ranked]


def draft_note(topic: str) -> dict:
    """Write an original Note on a topic, in your voice, with an archive link if relevant."""
    voice_profile = _load("voice_profile.json")
    content_index = _load("content_index.json")

    # Find most relevant article
    rel_raw = client.messages.create(
        model=MODEL, max_tokens=1024, system=RELEVANCE_SYSTEM,
        messages=[{"role": "user", "content":
            f"INCOMING: {topic}\n\nINDEX:\n{json.dumps(content_index)}"}],
    ).content[0].text
    related = json.loads(_strip_fences(rel_raw))

    prompt = (
        f"VOICE PROFILE:\n{json.dumps(voice_profile)}\n\n"
        f"TOPIC: {topic}\n\n"
        f"OPTIONAL ARTICLE TO LINK (only if it truly fits):\n{json.dumps(related)}\n\n"
        "Write ONE Substack Note (40-80 words). Sharp, standalone observation. "
        "If linking an article, put the URL on its own line at the end. "
        "Sound EXACTLY like the voice profile. "
        'Return ONLY JSON: {"note": "..."}'
    )
    resp = client.messages.create(
        model=MODEL, max_tokens=1024, system=NOTE_GEN_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    try:
        raw = _strip_fences(resp.content[0].text)
        parsed = json.loads(raw)
        if "note" in parsed:
            return {"drafts": [parsed["note"]]}
        elif "drafts" in parsed:
            return parsed
        else:
            return {"drafts": [raw]}
    except json.JSONDecodeError:
        return {"drafts": [resp.content[0].text]}


def daily_notes(n: int = 5) -> list[dict]:
    """Generate N Notes mined from your own archive."""
    voice_profile = _load("voice_profile.json")
    content_index = _load("content_index.json")
    picks = random.sample(content_index, min(n, len(content_index)))

    def _one(article):
        prompt = (
            f"VOICE PROFILE:\n{json.dumps(voice_profile)}\n\n"
            f"ARTICLE:\n{json.dumps(article)}\n\n"
            "Write ONE Substack Note (40-80 words) based on this article. "
            "Sharp standalone observation, then the URL on its own line at the end. "
            "Sound EXACTLY like the voice profile. "
            'Return ONLY JSON: {"note": "..."}'
        )
        try:
            resp = client.messages.create(
                model=MODEL, max_tokens=1024, system=NOTE_GEN_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = _strip_fences(resp.content[0].text)
            parsed = json.loads(raw)
            # Handle both {"note": "..."} and legacy {"drafts": ["..."]}
            if "note" in parsed:
                note_text = parsed["note"]
            elif "drafts" in parsed and parsed["drafts"]:
                note_text = parsed["drafts"][0]
            else:
                note_text = raw  # last-resort fallback
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            note_text = f"[⚠️  Could not parse draft for this article — {type(e).__name__}]"

        return {"article_title": article["title"],
                "article_url": article["url"],
                "note": note_text}

    with ThreadPoolExecutor(max_workers=len(picks)) as pool:
        return list(pool.map(_one, picks))


# ============================================================
# CLI
# ============================================================
def cmd_setup(args):
    print(f"📡 Fetching articles from {args.feed}")
    articles = fetch_articles(args.feed, limit=args.limit)
    print(f"   Got {len(articles)} articles")

    print("🎙️  Distilling voice profile (this is the expensive one-time step)…")
    voice = distill_voice(articles)
    _save("voice_profile.json", voice)
    print("   ✓ Saved data/voice_profile.json")

    print("📚 Indexing your archive in parallel…")
    index = build_content_index(articles)
    _save("content_index.json", index)
    print(f"   ✓ Saved data/content_index.json ({len(index)} entries)")

    print("\n✅ Setup complete. Try:")
    print('   python substack_assist.py reply --text "..."')
    print('   python substack_assist.py note --topic "..."')
    print('   python substack_assist.py daily-notes')


def cmd_reply(args):
    drafts = reply(args.text)
    print("\n" + "=" * 60)
    for i, d in enumerate(drafts, 1):
        print(f"\n[ Draft {i} — voice score {d['voice_score']}/10 ]")
        print(d["draft"])
    print("\n" + "=" * 60)


def cmd_note(args):
    result = draft_note(args.topic)
    print("\n" + "=" * 60)
    print(result["drafts"][0])
    print("=" * 60)


def cmd_daily_notes(args):
    notes = daily_notes(args.n)
    print(f"\n🗞️  Today's {len(notes)} drafted notes:\n")
    for i, n in enumerate(notes, 1):
        print(f"[ Note {i} — from: {n['article_title']} ]")
        print(n["note"])
        print()


def main():
    p = argparse.ArgumentParser(prog="substack-assist")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("setup", help="Index your Substack and extract voice profile")
    s.add_argument("--feed", required=True, help="RSS feed URL, e.g. https://yourname.substack.com/feed")
    s.add_argument("--limit", type=int, default=30)
    s.set_defaults(func=cmd_setup)

    s = sub.add_parser("reply", help="Draft 3 replies to a comment or Note in your voice")
    s.add_argument("--text", required=True)
    s.set_defaults(func=cmd_reply)

    s = sub.add_parser("note", help="Write an original Note on a topic in your voice")
    s.add_argument("--topic", required=True)
    s.set_defaults(func=cmd_note)

    s = sub.add_parser("daily-notes", help="Generate N Notes mined from your archive")
    s.add_argument("--n", type=int, default=5)
    s.set_defaults(func=cmd_daily_notes)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
