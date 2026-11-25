#!/usr/bin/env python3
"""Track a red apple and composite a simple gravity graphic over a video."""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import tempfile
from fractions import Fraction
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent
DEFAULT_INPUT = ROOT / "assets" / "video" / "apple-source.mp4"
DEFAULT_OUTPUT = ROOT / "output" / "apple-gravity-overlay.mp4"
DEFAULT_PREVIEW = ROOT / "output" / "apple-gravity-preview.png"
TRACK_WIDTH = 320
SAMPLE_FPS = 4
REFERENCE_WIDTH = 1280
REFERENCE_HEIGHT = 720


def command_output(command: list[str]) -> str:
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def probe_video(path: Path) -> tuple[int, int, float, Fraction, bool]:
    metadata = json.loads(
        command_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type,width,height,avg_frame_rate,r_frame_rate,duration",
                "-of",
                "json",
                str(path),
            ]
        )
    )
    video = next(
        (stream for stream in metadata["streams"] if stream.get("codec_type") == "video"),
        None,
    )
    if video is None:
        raise ValueError(f"No video stream found in {path}.")

    width = int(video["width"])
    height = int(video["height"])
    duration_value = video.get("duration") or metadata.get("format", {}).get("duration")
    if not duration_value:
        raise ValueError(f"Could not read the video duration from {path}.")

    rate_value = video.get("avg_frame_rate")
    if not rate_value or rate_value == "0/0":
        rate_value = video.get("r_frame_rate")
    if not rate_value or rate_value == "0/0":
        raise ValueError(f"Could not read the frame rate from {path}.")

    frame_rate = Fraction(rate_value)
    has_audio = any(
        stream.get("codec_type") == "audio" for stream in metadata["streams"]
    )
    return width, height, float(duration_value), frame_rate, has_audio


def apple_center(path: Path, output_width: int, output_height: int) -> tuple[float, float]:
    image = Image.open(path).convert("RGB")
    tracking_width, tracking_height = image.size
    pixels = image.load()
    mask = bytearray(tracking_width * tracking_height)

    for y in range(tracking_height):
        for x in range(tracking_width):
            red, green, blue = pixels[x, y]
            if red > 75 and red > green * 1.18 and red > blue * 1.1 and green < 180:
                mask[y * tracking_width + x] = 1

    visited = bytearray(len(mask))
    largest: tuple[int, int, int] | None = None
    for start, is_red in enumerate(mask):
        if not is_red or visited[start]:
            continue

        visited[start] = 1
        pending = [start]
        area = sum_x = sum_y = 0
        while pending:
            index = pending.pop()
            x = index % tracking_width
            y = index // tracking_width
            area += 1
            sum_x += x
            sum_y += y

            for next_y in range(max(0, y - 1), min(tracking_height, y + 2)):
                for next_x in range(max(0, x - 1), min(tracking_width, x + 2)):
                    neighbour = next_y * tracking_width + next_x
                    if mask[neighbour] and not visited[neighbour]:
                        visited[neighbour] = 1
                        pending.append(neighbour)

        if largest is None or area > largest[0]:
            largest = (area, sum_x, sum_y)

    if largest is None or largest[0] < 5:
        raise RuntimeError(
            f"Could not locate a red apple in tracking frame {path.name}. "
            "This example uses colour-based tracking; see README.md."
        )

    area, sum_x, sum_y = largest
    return (
        sum_x / area * output_width / tracking_width,
        sum_y / area * output_height / tracking_height,
    )


def interpolated_center(
    centers: list[tuple[float, float]], timestamp: float
) -> tuple[float, float]:
    position = min(timestamp * SAMPLE_FPS, len(centers) - 1)
    left = math.floor(position)
    right = min(left + 1, len(centers) - 1)
    fraction = position - left
    return tuple(
        centers[left][axis] * (1 - fraction) + centers[right][axis] * fraction
        for axis in (0, 1)
    )


def load_font(font_path: Path | None, size: int) -> ImageFont.FreeTypeFont:
    if font_path is not None:
        if not font_path.is_file():
            raise FileNotFoundError(f"Font file not found: {font_path}")
        return ImageFont.truetype(str(font_path), size)

    candidates = [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)

    raise FileNotFoundError(
        "Could not find a supported system font. Pass a TrueType font with --font."
    )


def draw_overlay(
    apple_x: float,
    apple_y: float,
    timestamp: float,
    width: int,
    height: int,
    font: ImageFont.FreeTypeFont,
) -> Image.Image:
    scale = min(width / REFERENCE_WIDTH, height / REFERENCE_HEIGHT)
    overlay = Image.new("RGBA", (width, height))
    draw = ImageDraw.Draw(overlay)

    opacity = min(1.0, max(0.0, timestamp / 0.45))
    alpha = round(255 * opacity)
    side_offset = 175 * scale
    edge_margin = 90 * scale
    preferred_x = apple_x + side_offset
    arrow_x = preferred_x if preferred_x <= width - edge_margin else apple_x - side_offset
    arrow_x = round(min(width - edge_margin, max(edge_margin, arrow_x)))
    arrow_top = round(max(40 * scale, apple_y - 40 * scale))
    arrow_tip = round(min(height - 18 * scale, apple_y + 105 * scale))
    shaft_end = max(arrow_top + 24 * scale, arrow_tip - 27 * scale)

    dark = (5, 18, 27, round(alpha * 0.9))
    cyan = (91, 231, 224, alpha)
    draw.line(
        (arrow_x, arrow_top, arrow_x, shaft_end),
        fill=dark,
        width=max(1, round(17 * scale)),
    )
    draw.line(
        (arrow_x, arrow_top, arrow_x, shaft_end),
        fill=cyan,
        width=max(1, round(8 * scale)),
    )
    draw.polygon(
        [
            (arrow_x, arrow_tip),
            (arrow_x - 22 * scale, shaft_end - 2 * scale),
            (arrow_x + 22 * scale, shaft_end - 2 * scale),
        ],
        fill=dark,
    )
    draw.polygon(
        [
            (arrow_x, arrow_tip - 5 * scale),
            (arrow_x - 15 * scale, shaft_end + scale),
            (arrow_x + 15 * scale, shaft_end + scale),
        ],
        fill=cyan,
    )

    label = "Gravity"
    text_box = draw.textbbox((0, 0), label, font=font)
    text_width = text_box[2] - text_box[0]
    label_width = text_width + 34 * scale
    label_height = 46 * scale
    label_left = arrow_x - label_width / 2
    label_top = max(12 * scale, arrow_top - label_height - 18 * scale)
    label_box = (
        label_left,
        label_top,
        label_left + label_width,
        label_top + label_height,
    )
    draw.rounded_rectangle(
        label_box,
        radius=18 * scale,
        fill=(5, 18, 27, round(alpha * 0.86)),
        outline=(91, 231, 224, alpha),
        width=max(1, round(2 * scale)),
    )
    draw.text(
        (arrow_x, label_top + label_height / 2),
        label,
        font=font,
        fill=(245, 252, 251, alpha),
        anchor="mm",
    )
    return overlay


def ffmpeg_rate(frame_rate: Fraction) -> str:
    return str(frame_rate.numerator) if frame_rate.denominator == 1 else str(frame_rate)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Track a red apple and overlay a downward gravity arrow."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Source video")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output video")
    parser.add_argument(
        "--preview",
        type=Path,
        default=DEFAULT_PREVIEW,
        help="Output path for a still showing the overlay",
    )
    parser.add_argument(
        "--preview-time",
        type=float,
        default=3.5,
        help="Time in seconds for the preview still",
    )
    parser.add_argument(
        "--font",
        type=Path,
        help="Optional TrueType font path (otherwise a common system font is used)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise RuntimeError("FFmpeg and FFprobe must both be installed and available on PATH.")
    if not args.input.is_file():
        raise FileNotFoundError(f"Source clip not found: {args.input}")
    if args.input.resolve() == args.output.resolve():
        raise ValueError("The output video path must be different from the source path.")

    width, height, duration, frame_rate, has_audio = probe_video(args.input)
    frame_count = max(1, round(duration * float(frame_rate)))
    tracking_width = min(TRACK_WIDTH, width)
    tracking_height = max(2, round(height * tracking_width / width / 2) * 2)
    scale = min(width / REFERENCE_WIDTH, height / REFERENCE_HEIGHT)
    font = load_font(args.font, max(12, round(27 * scale)))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.preview.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="gravity-overlay-") as temporary:
        work = Path(temporary)
        tracking_pattern = work / "track-%04d.png"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-i",
                str(args.input),
                "-vf",
                f"fps={SAMPLE_FPS},scale={tracking_width}:{tracking_height}",
                str(tracking_pattern),
            ],
            check=True,
        )
        tracking_frames = sorted(work.glob("track-*.png"))
        if not tracking_frames:
            raise RuntimeError("FFmpeg did not extract any apple-tracking frames.")
        centers = [apple_center(path, width, height) for path in tracking_frames]

        latest_preview_time = max(0, duration - 1 / float(frame_rate))
        preview_time = min(max(args.preview_time, 0), latest_preview_time)
        preview_frame = min(round(preview_time * float(frame_rate)), frame_count - 1)
        preview_time = preview_frame / float(frame_rate)
        preview_x, preview_y = interpolated_center(centers, preview_time)
        preview_overlay = work / "preview-overlay.png"
        draw_overlay(
            preview_x, preview_y, preview_time, width, height, font
        ).save(preview_overlay)

        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-ss",
                f"{preview_time:.6f}",
                "-i",
                str(args.input),
                "-i",
                str(preview_overlay),
                "-filter_complex",
                "[0:v][1:v]overlay=0:0:shortest=1",
                "-frames:v",
                "1",
                str(args.preview),
            ],
            check=True,
        )

        overlay_pattern = work / "overlay-%05d.png"
        for frame in range(frame_count):
            timestamp = frame / float(frame_rate)
            center_x, center_y = interpolated_center(centers, timestamp)
            draw_overlay(center_x, center_y, timestamp, width, height, font).save(
                work / f"overlay-{frame + 1:05d}.png"
            )

        command = [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(args.input),
            "-framerate",
            ffmpeg_rate(frame_rate),
            "-i",
            str(overlay_pattern),
            "-filter_complex",
            "[0:v][1:v]overlay=0:0:shortest=1,format=yuv420p[v]",
            "-map",
            "[v]",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-crf",
            "18",
            "-preset",
            "medium",
        ]
        if has_audio:
            command.extend(["-c:a", "copy"])
        command.extend(["-movflags", "+faststart", str(args.output)])
        subprocess.run(command, check=True)

    print(f"Rendered: {args.output}")
    print(f"Preview: {args.preview}")


if __name__ == "__main__":
    main()
