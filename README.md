# Substack Assistant

A multi-agent AI assistant that drafts Substack Notes, replies to comments, and mines your archive — all in **your writing voice**.

Gives you back ~8 hours a week on Substack engagement work.

## What it does

- **Replies to comments and Notes** in your voice, with optional archive links woven in naturally
- **Writes original Notes** on any topic, using your voice profile
- **Generates a daily batch of 5 Notes** mined from your own back catalogue, each linking to a relevant old article
- **Ranks every draft** by voice-fit score so you pick the best one in seconds

## Architecture

Seven focused agents across two pipelines.

```
SETUP PIPELINE (one-time, ~5 min)
    RSS feed → Voice Distiller → voice_profile.json
             → Content Indexer → content_index.json

DAILY PIPELINE (runs every time)
    Input → Context → Relevance → Drafter → Voice Check → 3 ranked drafts
```

| Agent            | Job                                           |
|------------------|-----------------------------------------------|
| Voice Distiller  | Extract your writing voice from your articles |
| Content Indexer  | Build a searchable index of your archive      |
| Context          | Understand what the incoming text means       |
| Relevance        | Find your articles related to the topic       |
| Drafter          | Write 3 voice-matched drafts                  |
| Voice Check      | Cold-read scoring for voice fit (0–10)        |
| Note Generator   | Turn archive articles into standalone Notes   |

## Quickstart

```bash
git clone https://github.com/DoraSzasz/substack-assistant
cd substack-assistant
pip install -r requirements.txt
export ANTHROPIC_API_KEY="sk-ant-..."
```

### One-time setup

```bash
python substack_assist.py setup --feed https://yourname.substack.com/feed
```

Reads your last 30 articles via RSS, extracts your voice profile, indexes your archive. Takes a few minutes and a few cents of API spend.

### Daily use

```bash
# Reply to any comment or Note in your voice
python substack_assist.py reply --text "Loved this — but isn't X actually Y?"

# Write an original Note on a topic
python substack_assist.py note --topic "why deep work is harder in 2026"

# Generate 5 Notes mined from your archive (each with a link back)
python substack_assist.py daily-notes
```

## Time savings

| Task                         | Before   | After    | Saved    |
|------------------------------|----------|----------|----------|
| Replying to article comments | ~2 hr    | ~30 min  | 1.5 hr   |
| Writing original Notes       | ~2.5 hr  | ~30 min  | 2 hr     |
| Replying to Notes            | ~2 hr    | ~30 min  | 1.5 hr   |
| Engaging on other writers    | ~2 hr    | ~45 min  | 1.25 hr  |
| Mining archive for links     | ~1.5 hr  | ~0 min   | 1.5 hr   |
| **Total per week**           | **~10 hr** | **~2.25 hr** | **~7.75 hr** |

## Keeping your voice

Five hard-learned rules:

1. Feed it your 15 **best** articles, not your 50 most recent
2. Re-run `setup` quarterly — your voice evolves
3. Always generate 3 drafts, never 1 — the side-by-side is the magic
4. Keep an `edits.log` of your changes to drafts, feed them back on next distillation
5. Never post unedited. This gets you 90% there; your eyes do the last 10%

## Extensions

- Tone flags (`--tone warm|sharp|curious`)
- "Disagree mode" for critical engagement without being brittle
- Cron `daily-notes` to email yourself the batch each morning
- Export voice profile as shareable JSON for use in other tools
- Hook up to actual Substack inbox via browser automation (next version)

## Companion article

Full tutorial and walkthrough: *[The Multi-Agent Substack Assistant That Gives Me Back 8 Hours a Week (And Actually Sounds Like Me)](#)*

## License

MIT — see LICENSE file.
