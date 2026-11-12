@echo off
REM Generate Azazael Ecosystem Video using FFmpeg

echo Creating Azazael Ecosystem Video...

REM Create temporary directory for frames
mkdir "C:\Users\clint\Desktop\CODEX\frames" 2>nul

REM Generate frames using Python (if available)
python "C:\Users\clint\Desktop\CODEX\generate_frames.py" 2>nul

if exist "C:\Users\clint\Desktop\CODEX\frames\frame_%04d.png" (
    echo Frames found. Creating video with FFmpeg...

    ffmpeg -y ^
        -framerate 30 ^
        -i "C:\Users\clint\Desktop\CODEX\frames\frame_%%04d.png" ^
        -c:v libx264 ^
        -pix_fmt yuv420p ^
        -crf 23 ^
        -preset medium ^
        "C:\Users\clint\Desktop\CODEX\Azazael_ECOSYSTEM.mp4"

    if exist "C:\Users\clint\Desktop\CODEX\Azazael_ECOSYSTEM.mp4" (
        echo.
        echo ================================================
        echo SUCCESS: Video created!
        echo Output: C:\Users\clint\Desktop\CODEX\Azazael_ECOSYSTEM.mp4
        echo ================================================
    ) else (
        echo Video generation failed.
    )

    REM Clean up frames
    rmdir /s /q "C:\Users\clint\Desktop\CODEX\frames" 2>nul
) else (
    echo No frames generated. Running FFmpeg fallback...

    REM Create video directly from color sources
    ffmpeg -y ^
        -f lavfi ^
        -i color=c=0x0a0a1a:s=1920x1080:d=5 ^
        -vf "drawtext=text='Azazael':fontcolor=cyan:fontsize=72:x=(w-text_w)/2:y=(h-text_h)/2:box=1:boxcolor=0x141428" ^
        -c:v libx264 ^
        -pix_fmt yuv420p ^
        -crf 23 ^
        -preset medium ^
        -t 5 ^
        "C:\Users\clint\Desktop\CODEX\Azazael_ECOSYSTEM.mp4"

    echo.
    echo ================================================
    echo Created basic placeholder video.
    echo For full video, install Python and run:
    echo python C:\Users\clint\Desktop\CODEX\generate_frames.py
    echo ================================================
)

pause
