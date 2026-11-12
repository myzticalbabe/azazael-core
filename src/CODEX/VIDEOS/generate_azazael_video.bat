@echo off
REM ============================================================================
REM Azazael Educational Video Generator - Simple FFmpeg Version
REM ============================================================================

setlocal

set "FFMPEG=C:\ProgramData\chocolatey\bin\ffmpeg.exe"
set "OUTPUT_DIR=%~dp0..\..\OUTPUT\videos"
set "TIMESTAMP=%date:~-4,4%%date:~-10,2%%date:~-7,2%_%time:~0,2%%time:~3,2%"

if not exist "%OUTPUT_DIR%" mkdir "%OUTPUT_DIR%"

echo ===============================================================================
echo AZAZAEL EDUCATIONAL VIDEO GENERATOR
echo ===============================================================================
echo.

REM Create intro
echo [1/7] Generating INTRO...
"%FFMPEG%" -y -f lavfi -i "color=c=0x0a0a1a:d=10" -vf "drawgrid=w=50:h=50:color=cyan@0.02,drawbox=x=0:y=0:w=1920:h=120:color=0x00d5ff:t=fill,drawtext=fontfile=C:\Windows\Fonts\consola.ttf:text='AZAZAEL ECOSYSTEM':fontcolor=white:fontsize=64:x=100:y=35,fade=t=in:st=0:d=2,fade=t=out:st=8:d=2" -c:v libx264 -pix_fmt yuv420p -t 10 "%OUTPUT_DIR%\azazael_intro_%TIMESTAMP%.mp4" 2>nul
echo   Done.

REM Create architecture
echo [2/7] Generating ARCHITECTURE...
"%FFMPEG%" -y -f lavfi -i "color=c=0x1a1a2e:d=15" -vf "drawgrid=w=200:h=100:color=magenta@0.02,drawbox=x=100:y=100:w=400:h=150:color=0x6a0dad:t=fill,drawtext=fontfile=C:\Windows\Fonts\consola.ttf:text='CORE LAYER':fontcolor=white:fontsize=36:x=150:y=150,drawbox=x=550:y=100:w=400:h=150:color=0x00d5ff:t=fill,drawtext=fontfile=C:\Windows\Fonts\consola.ttf:text='HYDRA ML':fontcolor=black:fontsize=36:x=600:y=150,drawbox=x=1000:y=100:w=400:h=150:color=0x00ff88:t=fill,drawtext=fontfile=C:\Windows\Fonts\consola.ttf:text='ORCHESTRATION':fontcolor=black:fontsize=36:x=1010:y=150,fade=t=in:st=0:d=3,fade=t=out:st=12:d=3" -c:v libx264 -pix_fmt yuv420p -t 15 "%OUTPUT_DIR%\azazael_architecture_%TIMESTAMP%.mp4" 2>nul
echo   Done.

REM Create Python section
echo [3/7] Generating PYTHON INSTALLATIONS...
"%FFMPEG%" -y -f lavfi -i "color=c=0x0d1a2d:d=12" -vf "drawgrid=w=100:h=50:color=cyan@0.015,drawbox=x=50:y=50:w=500:h=80:color=0x00bfff:t=fill,drawtext=fontfile=C:\Windows\Fonts\consola.ttf:text='PYTHON 3.14.3':fontcolor=white:fontsize=48:x=80:y=75,drawbox=x=50:y=180:w=500:h=80:color=0x4a90d9:t=fill,drawtext=fontfile=C:\Windows\Fonts\consola.ttf:text='PYTHON 3.11.9':fontcolor=white:fontsize=48:x=80:y=205,drawbox=x=50:y=310:w=500:h=80:color=0x2ecc71:t=fill,drawtext=fontfile=C:\Windows\Fonts\consola.ttf:text='18 TOTAL INSTALLATIONS':fontcolor=black:fontsize=40:x=80:y=335,fade=t=in:st=0:d=2,fade=t=out:st=10:d=2" -c:v libx264 -pix_fmt yuv420p -t 12 "%OUTPUT_DIR%\azazael_python_%TIMESTAMP%.mp4" 2>nul
echo   Done.

REM Create worms section
echo [4/7] Generating WORMS...
"%FFMPEG%" -y -f lavfi -i "color=c=0x1a0a2e:d=15" -vf "drawgrid=w=80:h=80:color=ff00ff@0.02,drawbox=x=100:y=100:w=350:h=60:color=0x9b59b6:t=fill,drawtext=fontfile=C:\Windows\Fonts\consola.ttf:text='OMNI WORM':fontcolor=white:fontsize=32:x=120:y=125,drawbox=x=100:y=200:w=350:h=60:color=0xe74c3c:t=fill,drawtext=fontfile=C:\Windows\Fonts\consola.ttf:text='MORPHING WORM':fontcolor=white:fontsize=32:x=120:y=225,drawbox=x=100:y=300:w=350:h=60:color=0x3498db:t=fill,drawtext=fontfile=C:\Windows\Fonts\consola.ttf:text='CLUSTER INSPECTOR':fontcolor=white:fontsize=32:x=120:y=325,drawbox=x=100:y=400:w=350:h=60:color=0x2ecc71:t=fill,drawtext=fontfile=C:\Windows\Fonts\consola.ttf:text='ERROR SCAN':fontcolor=black:fontsize=32:x=120:y=425,fade=t=in:st=0:d=3,fade=t=out:st=12:d=3" -c:v libx264 -pix_fmt yuv420p -t 15 "%OUTPUT_DIR%\azazael_worms_%TIMESTAMP%.mp4" 2>nul
echo   Done.

REM Create ML section
echo [5/7] Generating ML FRAMEWORKS...
"%FFMPEG%" -y -f lavfi -i "color=c=0x0a2e1a:d=12" -vf "drawgrid=w=150:h=60:color=00ff88@0.02,drawbox=x=50:y=80:w=280:h=70:color=0xff6b6b:t=fill,drawtext=fontfile=C:\Windows\Fonts\consola.ttf:text='TENSORFLOW 2.20':fontcolor=white:fontsize=28:x=70:y=105,drawbox=x=350:y=80:w=280:h=70:color=0xfeca57:t=fill,drawtext=fontfile=C:\Windows\Fonts\consola.ttf:text='PYTORCH 2.10':fontcolor=black:fontsize=28:x=370:y=105,drawbox=x=650:y=80:w=280:h=70:color=0x5f27cd:t=fill,drawtext=fontfile=C:\Windows\Fonts\consola.ttf:text='SCIKIT 1.8':fontcolor=white:fontsize=28:x=670:y=105,drawbox=x=950:y=80:w=280:h=70:color=0x00d2d3:t=fill,drawtext=fontfile=C:\Windows\Fonts\consola.ttf:text='KERAS 3.13':fontcolor=black:fontsize=28:x=970:y=105,fade=t=in:st=0:d=2,fade=t=out:st=10:d=2" -c:v libx264 -pix_fmt yuv420p -t 12 "%OUTPUT_DIR%\azazael_ml_%TIMESTAMP%.mp4" 2>nul
echo   Done.

REM Create LLM section
echo [6/7] Generating LLM INTEGRATION...
"%FFMPEG%" -y -f lavfi -i "color=c=0x2e1a2e:d=12" -vf "drawgrid=w=120:h=60:color=ff00ff@0.02,drawbox=x=200:y=100:w=500:h=80:color=0x6c5ce7:t=fill,drawtext=fontfile=C:\Windows\Fonts\consola.ttf:text='KILO-LOCAL CLI':fontcolor=white:fontsize=40:x=220:y=125,drawbox=x=200:y=220:w=500:h=60:color=0x00cec9:t=fill,drawtext=fontfile=C:\Windows\Fonts\consola.ttf:text='OLLAMA INTEGRATION':fontcolor=black:fontsize=28:x=220:y=245,drawbox=x=200:y=320:w=500:h=60:color=0xfd79a8:t=fill,drawtext=fontfile=C:\Windows\Fonts\consola.ttf:text='OPEN INTERPRETER':fontcolor=black:fontsize=28:x=220:y=345,fade=t=in:st=0:d=2,fade=t=out:st=10:d=2" -c:v libx264 -pix_fmt yuv420p -t 12 "%OUTPUT_DIR%\azazael_llm_%TIMESTAMP%.mp4" 2>nul
echo   Done.

REM Create outro
echo [7/7] Generating OUTRO...
"%FFMPEG%" -y -f lavfi -i "color=c=0x0a0a1a:d=8" -vf "drawgrid=w=60:h=60:color=cyan@0.03,drawbox=x=0:y=0:w=1920:h=1080:color=0x000000@0.5:t=fill,drawtext=fontfile=C:\Windows\Fonts\consola.ttf:text='AZAZAEL ECOSYSTEM':fontcolor=0x00d5ff:fontsize=72:x=center:y=center,fade=t=in:st=0:d=2,fade=t=out:st=6:d=2" -c:v libx264 -pix_fmt yuv420p -t 8 "%OUTPUT_DIR%\azazael_outro_%TIMESTAMP%.mp4" 2>nul
echo   Done.

REM Create concat file
echo.
echo Creating concat file...
(
echo file 'azazael_intro_%TIMESTAMP%.mp4'
echo file 'azazael_architecture_%TIMESTAMP%.mp4'
echo file 'azazael_python_%TIMESTAMP%.mp4'
echo file 'azazael_worms_%TIMESTAMP%.mp4'
echo file 'azazael_ml_%TIMESTAMP%.mp4'
echo file 'azazael_llm_%TIMESTAMP%.mp4'
echo file 'azazael_outro_%TIMESTAMP%.mp4'
) > "%OUTPUT_DIR%\video_list_%TIMESTAMP%.txt"

REM Concatenate
echo Concatenating all sections...
"%FFMPEG%" -y -f concat -safe 0 -i "%OUTPUT_DIR%\video_list_%TIMESTAMP%.txt" -c:v libx264 -pix_fmt yuv420p -crf 23 -preset medium "%OUTPUT_DIR%\azazael_educational_video_%TIMESTAMP%.mp4" 2>nul

echo.
echo ===============================================================================
echo VIDEO GENERATION COMPLETE
echo ===============================================================================
echo.
echo Output directory: %OUTPUT_DIR%
echo Final video: azazael_educational_video_%TIMESTAMP%.mp4
echo.
dir "%OUTPUT_DIR%\*.mp4" 2>nul

endlocal
pause
