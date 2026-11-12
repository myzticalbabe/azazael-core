@echo off
REM Generate Azazael Ecosystem Video using FFmpeg
REM Pure FFmpeg solution - no Python required

echo ================================================
echo Azazael Ecosystem Video Generator
echo ================================================
echo.

set OUTPUT=C:\Users\clint\Desktop\CODEX\Azazael_ECOSYSTEM.mp4
set DURATION=60
set FPS=30

REM Check if this is a quick test or full render
if "%1"=="quick" (
    set DURATION=10
    echo [QUICK MODE] Creating 10-second preview...
) else (
    echo [FULL MODE] Creating 60-second video...
)

echo Output: %OUTPUT%
echo Duration: %DURATION% seconds
echo FPS: %FPS%
echo.

REM Create animated video using FFmpeg
ffmpeg -y ^
    -f lavfi -i "color=c=0x0a0a1a:s=1920x1080:d=%DURATION%" ^
    -f lavfi -i "frei0r=gradient_source:0=1920:1=1080" ^
    -filter_complex "[0][1]blend=all_op='softlight':c0_mode='src':c1_mode='src'[bg];^
[bg]fps=%FPS%,drawgrid=w=50:h=50:color=cyan@0.03:vh=1:vw=1[grid];^
[grid]drawtext=fontfile=/Windows/Fonts/consola.ttf:text='Azazael':fontcolor=cyan@0.1:fontsize=200:x=(w-text_w)/2:y=(h-text_h)/2:titletext[A];^
[A]drawtext=fontfile=/Windows/Fonts/consola.ttf:text='ECOSYSTEM':fontcolor=magenta@0.1:fontsize=100:x=(w-text_w)/2:y=(h-text_h)/2+150[B];^
[B]drawtext=fontfile=/Windows/Fonts/consola.ttf:text='14 Systems':fontcolor=cyan:fontsize=48:x=200:y=300:t=if(between(t\,0\,5)\,0\,1)[txt1];^
[txt1]drawtext=fontfile=/Windows/Fonts/consola.ttf:text='17 Worms':fontcolor=green:fontsize=48:x=200:y=380:t=if(between(t\,5\,10)\,0\,1)[txt2];^
[txt2]drawtext=fontfile=/Windows/Fonts/consola.ttf:text='7 Tiers':fontcolor=yellow:fontsize=48:x=200:y=460:t=if(between(t\,10\,15)\,0\,1)[txt3];^
[txt3]drawtext=fontfile=/Windows/Fonts/consola.ttf:text='24 Animations':fontcolor=magenta:fontsize=48:x=200:y=540:t=if(between(t\,15\,20)\,0\,1)[txt4];^
[txt4]drawtext=fontfile=/Windows/Fonts/consola.ttf:text='TIER -1: OS Congruency':fontcolor=0xff00aa:fontsize=32:x=100:y=750:t=if(between(t\,20\,25)\,0\,1)[t0];^
[t0]drawtext=fontfile=/Windows/Fonts/consola.ttf:text='TIER 0: Concurrency':fontcolor=0x00f5ff:fontsize=32:x=100:y=800:t=if(between(t\,22\,27)\,0\,1)[t1];^
[t1]drawtext=fontfile=/Windows/Fonts/consola.ttf:text='TIER 1-2: Worms & Clusters':fontcolor=0xff4757:fontsize=32:x=100:y=850:t=if(between(t\,24\,29)\,0\,1)[t2];^
[t2]drawtext=fontfile=/Windows/Fonts/consola.ttf:text='TIER 3: METAWORMS':fontcolor=0xa855f7:fontsize=32:x=100:y=900:t=if(between(t\,26\,31)\,0\,1)[t3];^
[t3]drawtext=fontfile=/Windows/Fonts/consola.ttf:text='TIER 4: HYDRAMETA ML':fontcolor=0xbf00ff:fontsize=32:x=100:y=950:t=if(between(t\,28\,33)\,0\,1)[t4];^
[t4]drawtext=fontfile=/Windows/Fonts/consola.ttf:text='TIER 5: Distillation':fontcolor=0xffdd00:fontsize=32:x=100:y=1000:t=if(between(t\,30\,35)\,0\,1)[t5];^
[t5]fade=t=out:st=58:d=2,fps=%FPS%" ^
    -c:v libx264 ^
    -pix_fmt yuv420p ^
    -crf 23 ^
    -preset medium ^
    -t %DURATION% ^
    "%OUTPUT%"

if exist "%OUTPUT%" (
    echo.
    echo ================================================
    echo SUCCESS: Video created!
    echo Output: %OUTPUT%
    echo Size: %~zx0 bytes
    echo ================================================
) else (
    echo.
    echo ERROR: Video generation failed.
    echo Check FFmpeg installation.
)

pause
