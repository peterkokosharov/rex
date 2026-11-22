# Build бележки — Windows машини

## Налични машини

### 2x Windows 10
- Могат да дръпнат кода и да компилират native модула.
- Препоръчително: **Build Tools for Visual Studio 2022** или **VS Community 2022** + workload **Desktop development with C++**.
- Build: виж `README-WINDOWS.md` (x64 препоръчително).

### 2x Windows 7
- Ограничение: **Python максимум 3.10, не може нагоре** (по думите на потребителя; да се провери точната версия на машината).
- За компилация на C++ native модул: по-новите VS/Build Tools може да не се поддържат добре на Win7. По-безопасен избор е **Visual Studio/Build Tools 2019** (ако е наличен/съвместим) или по-стар MSVC toolchain, който поддържа Win7.
- `rex_native` е C++17; VS 2019 MSVC го поддържа.
- Ако native build е проблемен на Win7: **не е блокер**. Python wrapper има fallback (`runtimelib/native/_fallback.py`) и `rex` работи без `.dll`.

## Практичен план

1. **Win10**: компилирай x64 DLL с MSVC 2022, тествай `tests.test_native` и `tests.test_native_integration`.
2. **Win7**: първо пробвай Python fallback (без DLL). Ако искаш native, пробвай VS 2019 Build Tools + CMake; компилирай DLL за съответната Python архитектура (x86/x64).
3. **Разпространение**: копирай съответния `rex_native.dll` в `runtimelib/native/`. Wrapper търси `.dll` автоматично.

## MSVC лиценз/достъп

- **Build Tools for Visual Studio** — безплатно.
- **Visual Studio Community** — безплатно за individuals/open-source/учебни/малки екипи (според лиценза на Microsoft).
- **Professional/Enterprise** — платени, не са нужни за този build.

Източник: https://visualstudio.microsoft.com/downloads/
