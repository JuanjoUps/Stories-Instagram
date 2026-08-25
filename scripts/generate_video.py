#!/usr/bin/env python3
"""
Genera un vídeo de story de Instagram (1080x1920) a partir de un guion JSON,
con audio TTS (Edge TTS, gratuito), subtítulos quemados sobre un banner
semitransparente, y fundido de entrada/salida entre segmentos.

Uso:
    python generate_video.py guion.json salida/story_final.mp4

Formato de guion.json:
{
  "segmentos": [
    {
      "media": "assets/villita_intro.mp4",
      "texto": "Hoy arranca la pretemporada del Benjamin",
      "voz": "es-ES-AlvaroNeural"
    },
    {
      "media": "assets/card_fecha.png",
      "texto": "Inicio: 7 de septiembre. Plazas disponibles",
      "voz": "es-ES-AlvaroNeural"
    }
  ]
}

Requisitos: pip install edge-tts   |   ffmpeg instalado en el sistema
"""

import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import edge_tts

WIDTH, HEIGHT = 1080, 1920
FADE = 0.4                   # segundos de fundido en cada corte (efecto "cortinilla")
BANNER_HEIGHT = 340           # alto del banner de subtitulos (px)
BANNER_BOTTOM_MARGIN = 260    # margen desde abajo para no chocar con la UI de Instagram
FONT_SIZE = 68                 # tamano de fuente de los subtitulos (px, sobre lienzo de 1920 de alto)
COLA_SEGUNDOS = 0.6           # margen extra tras terminar el audio de cada segmento


async def generar_audio_srt(texto, voz, out_mp3, out_srt):
    communicate = edge_tts.Communicate(texto, voz)
    submaker = edge_tts.SubMaker()
    with open(out_mp3, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] in ("WordBoundary", "SentenceBoundary"):
                submaker.feed(chunk)
    with open(out_srt, "w", encoding="utf-8") as f:
        f.write(submaker.get_srt())


def duracion_audio(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True
    )
    return float(r.stdout.strip())


def es_video(path):
    return Path(path).suffix.lower() in (".mp4", ".mov", ".m4v")


def construir_segmento(media_path, audio_path, srt_path, duracion, out_path):
    """Crea un clip de duracion fija: imagen o video, redimensionado a
    1080x1920, con subtitulo quemado sobre un banner semitransparente,
    audio TTS, y fundido de entrada/salida."""

    banner_y = HEIGHT - BANNER_BOTTOM_MARGIN - BANNER_HEIGHT
    srt_escapado = str(srt_path).replace("\\", "/").replace(":", "\\:")

    vf = (
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH}:{HEIGHT},"
        f"drawbox=x=0:y={banner_y}:w={WIDTH}:h={BANNER_HEIGHT}:color=black@0.45:t=fill,"
        f"subtitles={srt_escapado}:force_style="
        f"'FontName=Arial,FontSize={FONT_SIZE},PrimaryColour=&HFFFFFF&,"
        f"OutlineColour=&H000000&,BorderStyle=1,Outline=4,"
        f"Alignment=2,MarginV={BANNER_BOTTOM_MARGIN + 40},"
        f"PlayResX={WIDTH},PlayResY={HEIGHT}',"
        f"fade=t=in:st=0:d={FADE},fade=t=out:st={duracion - FADE}:d={FADE}"
    )

    if es_video(media_path):
        cmd = [
            "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(media_path),
            "-i", str(audio_path),
            "-t", str(duracion),
            "-vf", vf,
            "-map", "0:v", "-map", "1:a",
            "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p",
            str(out_path)
        ]
    else:
        cmd = [
            "ffmpeg", "-y", "-loop", "1", "-i", str(media_path),
            "-i", str(audio_path),
            "-t", str(duracion),
            "-vf", vf,
            "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p",
            "-shortest",
            str(out_path)
        ]
    subprocess.run(cmd, check=True)


def concatenar(clips, out_path):
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        for c in clips:
            f.write(f"file '{Path(c).resolve()}'\n")
        listado = f.name
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listado,
         "-c", "copy", str(out_path)],
        check=True
    )


def main():
    if len(sys.argv) != 3:
        print("Uso: python generate_video.py guion.json salida/story_final.mp4")
        sys.exit(1)

    guion_path, salida_path = sys.argv[1], sys.argv[2]
    guion = json.loads(Path(guion_path).read_text(encoding="utf-8"))
    base_dir = Path(guion_path).parent  # las rutas "media" son relativas al guion

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        clips = []
        for i, seg in enumerate(guion["segmentos"]):
            mp3 = tmp / f"seg{i}.mp3"
            srt = tmp / f"seg{i}.srt"
            asyncio.run(generar_audio_srt(
                seg["texto"], seg.get("voz", "es-ES-AlvaroNeural"), mp3, srt
            ))
            dur = duracion_audio(mp3) + COLA_SEGUNDOS
            clip = tmp / f"clip{i}.mp4"
            media_path = base_dir / seg["media"]
            construir_segmento(media_path, mp3, srt, dur, clip)
            clips.append(clip)

        Path(salida_path).parent.mkdir(parents=True, exist_ok=True)
        concatenar(clips, salida_path)

    print(f"Video generado: {salida_path}")


if __name__ == "__main__":
    main()
