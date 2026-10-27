"""Generate synthetic macOS TTS demo. Not licensed research corpus or authentic person."""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path, help="新建的測試資料目錄")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    aiff = output / "synthetic.aiff"
    wav = output / "synthetic.wav"
    phrase = "你好，這是一段合成語音。測試字幕與音段處理。"
    subprocess.run(["say", "-v", "Meijia", "-o", str(aiff), phrase], check=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(aiff), "-ac", "1", "-ar", "16000", str(wav)], check=True)
    aiff.unlink()
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                             "-of", "default=noprint_wrappers=1:nokey=1", str(wav)], check=True,
                            text=True, capture_output=True)
    duration = float(result.stdout.strip())
    midpoint = duration / 2
    def stamp(seconds):
        millis = round(seconds * 1000)
        return f"{millis // 3600000:02d}:{millis // 60000 % 60:02d}:{millis // 1000 % 60:02d},{millis % 1000:03d}"
    (output / "synthetic.srt").write_text(f"1\n{stamp(0)} --> {stamp(midpoint)}\n你好，這是一段合成語音。\n\n"
                                            f"2\n{stamp(midpoint)} --> {stamp(duration)}\n測試字幕與音段處理。\n", encoding="utf-8")
    (output / "README.txt").write_text("測試 fixture：macOS 系統 Meijia 合成語音及依時長自動切半的合成字幕。"
                                      "時間界線可能不準，不能作研究語料、真實台灣語者或人工 gold。\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
