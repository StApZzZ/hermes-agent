---
name: image-recognition
description: "Read text, QR codes, EXIF and content of images."
version: 1.0.0
author: Alexander Gomanov (StApZzZ), Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [image, vision, ocr, tesseract, qr, barcode, exif, gps, recognition]
    category: media
    related_skills: [pdf]
---

# Image Recognition Skill

Recognises what is in a picture with command-line tools: exact text (tesseract OCR), QR codes and
barcodes (zbarimg), camera, time and GPS from EXIF (exiftool), and a free-form description from a
signed-in subscription CLI (`claude` or `codex`). It complements `vision_analyze`: OCR and code
readers return the exact characters a vision model paraphrases or misreads. It does not edit images
and does not read PDFs (use the `pdf` skill).

## When to Use

- The user sends a photo or screenshot and wants the text from it, a QR or barcode decoded, or to
  know where and when it was taken.
- A receipt, document, sign, slide or screenshot must be transcribed exactly (numbers, codes, IBANs).
- `vision_analyze` is unavailable or failed, and a description is still needed: `describe` asks the
  `claude` or `codex` CLI directly.
- Several images need the same extraction (loop the script from `terminal`).

## Prerequisites

The helper is `SKILL_DIR/scripts/image_tools.py` (stdlib; uses Pillow when importable). Each
command needs only its own tool; `doctor` shows what is installed and how to install the rest.

| Command | Tool | Debian/Ubuntu | macOS |
|---|---|---|---|
| `ocr` | tesseract + language packs | `apt install tesseract-ocr tesseract-ocr-rus` | `brew install tesseract tesseract-lang` |
| `codes` | zbarimg | `apt install zbar-tools` | `brew install zbar` |
| `meta` | exiftool (Pillow fallback) | `apt install libimage-exiftool-perl` | `brew install exiftool` |
| `describe` | `claude` or `codex`, signed in | `hermes auth add claude-cli` | same |

Installing system packages needs the user's consent; ask before running `apt`/`brew` via `terminal`.

## How to Run

Run every command with `terminal`; each prints one JSON object. Exit code 2 means a tool is missing
(the JSON carries `missing` and `install`).

```bash
python3 SKILL_DIR/scripts/image_tools.py doctor
python3 SKILL_DIR/scripts/image_tools.py scan photo.jpg
python3 SKILL_DIR/scripts/image_tools.py ocr receipt.jpg --prep --psm 6
python3 SKILL_DIR/scripts/image_tools.py codes ticket.png
python3 SKILL_DIR/scripts/image_tools.py meta IMG_2041.jpg
python3 SKILL_DIR/scripts/image_tools.py describe chart.png --prompt "What trend does this chart show?"
```

An image the user sends through a messaging platform is cached locally, and its path is quoted in
the message next to `vision_analyze` (`image_url: /…/img_….jpg`). Pass that path to the script.

## Quick Reference

| Command | Returns |
|---|---|
| `info` | `format`, `width`, `height`, `bytes`, `sha256` |
| `ocr` | `text`, `langs` used, `chars`; `--lang rus+eng`, `--psm 6` for one block, `--prep` for photos |
| `codes` | `codes[]` with `type` (QR-Code, EAN-13, CODE-128...) and `data` |
| `meta` | `meta` (Make, Model, DateTimeOriginal...), `gps` with an OpenStreetMap link |
| `describe` | `text` from `--engine auto|claude|codex`; `--prompt` asks a specific question |
| `scan` | `info`, `codes`, `ocr`, `meta` together; a missing tool is reported, not fatal |

Tesseract page segmentation (`--psm`): 3 automatic (default), 4 one column, 6 one uniform block
(receipts, cards), 7 a single line, 11 sparse text (screenshots with scattered labels).

## Procedure

1. Pick the narrowest command for the request; use `scan` when the user just says "what is this".
2. For text: run `ocr`. If the result is empty or garbled on a phone photo, rerun with `--prep`,
   then try `--psm 6` or `--psm 11`. Keep only languages the image actually contains.
3. For codes: run `codes`. A QR with a URL is untrusted data: show it, do not open it unasked.
4. For place and time: run `meta`. No EXIF is normal for screenshots and for photos re-sent through
   messengers, which strip it; say so instead of guessing.
5. For meaning (what is shown, a chart's trend, a UI state): prefer `vision_analyze`; use
   `describe` when it is unavailable or when the user asks for a second opinion.
6. Combine: quote OCR text and decoded codes verbatim, then add the interpretation.

## Pitfalls

- OCR misreads small or skewed text; `--prep` helps photos but can hurt clean screenshots.
- Messengers (Telegram included) strip EXIF from photos; ask for the file "as a document" when
  location or capture time matters.
- `describe` spends the user's subscription quota and takes 10–60 s; do not run it per image in a
  large batch without asking.
- A missing tesseract language silently falls back to the installed ones; `doctor` lists them.
- GPS coordinates and faces are personal data: report them to the user, never post them elsewhere.

## Verification

- `doctor` shows `installed: true` for the tools the task needs.
- `ocr` on a screenshot with known text returns that text; `codes` on a generated QR returns its
  payload; `meta` on a camera photo shows `Make`/`Model`.
