@echo off
rem Gera o ManyVRCX.exe (precisa de Python + PyInstaller). O build-site copia o .exe para a pagina.
cd /d "%~dp0"
python -m PyInstaller --noconfirm --onefile --windowed --name ManyVRCX --icon "%cd%\many.ico" --add-data "%cd%\many.ico;." --distpath . --workpath build --specpath build extrator.py
rmdir /s /q build
