Sources for `../brag.mp4`: `index.html` draws every frame as a function of time (`renderAt(t)`),
`capture.mjs` renders it with Playwright, and `music.py` writes the soundtrack. To rebuild, copy
the console screenshots (`incidents.png`, `incident.png`) and the Geist fonts into this folder,
then run:

    node capture.mjs frames 20.5
    uv run --no-project --with numpy==2.1.3 python music.py
    cp frames/f0084.jpg ../brag.jpg && cp frames/f0084.jpg frames/f0000.jpg   # poster = frame 0
    ffmpeg -y -framerate 30 -i frames/f%04d.jpg -i music.wav -c:v libx264 -preset slow -crf 18 \
      -pix_fmt yuv420p -movflags +faststart -c:a aac -b:a 192k -shortest ../brag.mp4
