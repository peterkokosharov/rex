#include "rex_native.h"
#include <cassert>
#include <cstdio>
#include <string>
#include <filesystem>
#include <fstream>
#include <iostream>

namespace fs = std::filesystem;

static std::string s(char* p) {
    std::string r = p ? p : "";
    rex_free(p);
    return r;
}

int main() {
    // json_escape
    assert(s(rex_json_escape("a\"b\\c\n")) == "a\\\"b\\\\c\\n");
    assert(s(rex_json_escape("")) == "");

    // json_get_string
    assert(s(rex_json_get_string("{\"type\":\"token\",\"text\":\"hi\"}", "type")) == "token");
    assert(s(rex_json_get_string("{\"type\":\"token\",\"text\":\"hi\"}", "text")) == "hi");
    assert(s(rex_json_get_string("{\"a\":\"line\\n2\"}", "a")) == "line\n2");
    assert(s(rex_json_get_string("{\"x\":1}", "missing")) == "");

    // parse_sse_data
    assert(rex_parse_sse_data("event: foo") == nullptr);
    {
        char* p = rex_parse_sse_data("data: {\"a\":1}");
        assert(p && std::string(p) == "{\"a\":1}");
        rex_free(p);
    }
    {
        char* p = rex_parse_sse_data("data: [DONE]");
        assert(p && std::string(p) == "[DONE]");
        rex_free(p);
    }

    // grep/glob on temp dir
    auto dir = fs::temp_directory_path() / "rex_native_test";
    fs::remove_all(dir);
    fs::create_directories(dir / "sub");
    {
        std::ofstream(dir / "a.txt") << "hello world\nbye\n";
        std::ofstream(dir / "sub" / "b.py") << "print('hello')\n";
    }
    std::string g = s(rex_grep(dir.string().c_str(), "hello", nullptr, nullptr, 100));
    assert(g.find("hello world") != std::string::npos);
    assert(g.find("print('hello')") != std::string::npos);

    std::string g2 = s(rex_grep(dir.string().c_str(), "hello", "*.txt", nullptr, 100));
    assert(g2.find("hello world") != std::string::npos);
    assert(g2.find("print('hello')") == std::string::npos);

    std::string gl = s(rex_glob(dir.string().c_str(), "**/*.py"));
    assert(gl.find("b.py") != std::string::npos);

    fs::remove_all(dir);
    std::cout << "ok\n";
    return 0;
}
