# Build на `rex_native` за Windows (MSVC)

Този модул е optional C++ native extension за `rex` (ctypes). Ако DLL липсва, Python ползва fallback.

## Файлове

Нужни са:

- `CMakeLists.txt`
- `rex_native.h`
- `rex_native.cpp`
- `test_native.cpp`

## Предварителни изисквания

- Windows 10/11
- Visual Studio 2019/2022 (или Build Tools for Visual Studio) с **Desktop development with C++**
- CMake 3.16+ (може да е от VS или отделно инсталиран)

## 1) 64-bit build (препоръчително, x64)

Отвори **"x64 Native Tools Command Prompt for VS 2022"** (или съответния за твоята VS версия).

```bat
cd path\to\rex\native
cmake -S . -B build-x64 -G "Visual Studio 17 2022" -A x64
cmake --build build-x64 --config Release
ctest --test-dir build-x64 -C Release --output-on-failure
```

За Visual Studio 2019 използвай:

```bat
cmake -S . -B build-x64 -G "Visual Studio 16 2019" -A x64
cmake --build build-x64 --config Release
ctest --test-dir build-x64 -C Release --output-on-failure
```

## 2) 32-bit build (ако ти трябва x86)

Отвори **"x86 Native Tools Command Prompt for VS 2022"** или ползвай `-A Win32`:

```bat
cd path\to\rex\native
cmake -S . -B build-x86 -G "Visual Studio 17 2022" -A Win32
cmake --build build-x86 --config Release
ctest --test-dir build-x86 -C Release --output-on-failure
```

> Използвай DLL, съответстваща на архитектурата на Python-а (64-bit Python → x64 DLL, 32-bit Python → x86 DLL).

## 3) Друг 64-bit компилатор / toolchain

### MSVC от Build Tools (без пълен VS IDE)

Инсталирай **Build Tools for Visual Studio** + C++ workload, после ползвай същите CMake команди от x64 Native Tools prompt.

### Ninja + MSVC

Ако имаш Ninja:

```bat
cd path\to\rex\native
cmake -S . -B build-ninja -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build build-ninja -j
ctest --test-dir build-ninja --output-on-failure
```

Пускай това от x64 Native Tools prompt, за да е активен правилният MSVC компилатор.

### clang-cl (ако е инсталиран във VS)

```bat
cd path\to\rex\native
cmake -S . -B build-clangcl -G "Visual Studio 17 2022" -A x64 -T ClangCL
cmake --build build-clangcl --config Release
ctest --test-dir build-clangcl -C Release --output-on-failure
```

## Къде е изходният DLL

`CMakeLists.txt` е настроен да пише библиотеката в `../runtimelib/native/`.

При multi-config Visual Studio generator, файлът може да се появи в:

```text
..\runtimelib\native\Release\rex_native.dll
```

или

```text
..\runtimelib\native\rex_native.dll
```

За използване от Python копирай DLL-а тук:

```text
rex\runtimelib\native\rex_native.dll
```

## Проверка от Python

От root-а на `rex`:

```bat
cd path\to\rex
set PYTHONPATH=%CD%
python -c "import runtimelib.native as n; print(n.backend(), n.NATIVE_AVAILABLE)"
python -m unittest tests.test_native tests.test_native_integration -v
```

Очаквано: `backend()` да е `native`, а тестовете да минат.

## Бележки

- Модулът използва C++17 и `std::filesystem`.
- Няма външни зависимости; няма `fnmatch.h`/POSIX зависимост.
- Ако DLL не се зареди, `rex` продължава да работи с Python fallback.
