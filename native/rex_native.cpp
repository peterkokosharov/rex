#include "rex_native.h"

#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>
#include <regex>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <algorithm>
#include <cctype>

namespace fs = std::filesystem;

static char* dup_cstr(const std::string& s) {
    char* p = (char*)std::malloc(s.size() + 1);
    if (!p) return nullptr;
    std::memcpy(p, s.c_str(), s.size() + 1);
    return p;
}

extern "C" void rex_free(void* p) { std::free(p); }

extern "C" char* rex_json_escape(const char* s) {
    if (!s) return dup_cstr("");
    std::string out;
    out.reserve(std::strlen(s) + 8);
    for (const unsigned char* p = (const unsigned char*)s; *p; ++p) {
        unsigned char c = *p;
        switch (c) {
            case '\\': out += "\\\\"; break;
            case '"': out += "\\\""; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            case '\b': out += "\\b"; break;
            case '\f': out += "\\f"; break;
            default:
                if (c < 0x20) {
                    char buf[8];
                    std::snprintf(buf, sizeof(buf), "\\u%04x", c);
                    out += buf;
                } else {
                    out += (char)c;
                }
        }
    }
    return dup_cstr(out);
}

static bool is_space(char c) { return c == ' ' || c == '\t' || c == '\r' || c == '\n'; }

extern "C" char* rex_json_get_string(const char* json, const char* key) {
    if (!json || !key) return dup_cstr("");
    std::string k = std::string("\"") + key + "\"";
    const char* p = std::strstr(json, k.c_str());
    if (!p) return dup_cstr("");
    p += k.size();
    while (*p && is_space(*p)) ++p;
    if (*p != ':') return dup_cstr("");
    ++p;
    while (*p && is_space(*p)) ++p;
    if (*p != '"') return dup_cstr("");
    ++p;
    std::string out;
    bool esc = false;
    for (; *p; ++p) {
        char c = *p;
        if (esc) {
            switch (c) {
                case 'n': out += '\n'; break;
                case 't': out += '\t'; break;
                case 'r': out += '\r'; break;
                case 'b': out += '\b'; break;
                case 'f': out += '\f'; break;
                default: out += c; break;
            }
            esc = false;
        } else if (c == '\\') {
            esc = true;
        } else if (c == '"') {
            break;
        } else {
            out += c;
        }
    }
    return dup_cstr(out);
}

extern "C" char* rex_parse_sse_data(const char* line) {
    if (!line) return nullptr;
    const char* p = line;
    while (*p == ' ' || *p == '\t') ++p;
    const char* prefix = "data:";
    if (std::strncmp(p, prefix, 5) != 0) return nullptr;
    p += 5;
    while (*p == ' ' || *p == '\t') ++p;
    // trim end \r\n
    std::string data(p);
    while (!data.empty() && (data.back() == '\n' || data.back() == '\r' || data.back() == ' ' || data.back() == '\t')) data.pop_back();
    if (data == "[DONE]") return dup_cstr("[DONE]");
    return dup_cstr(data);
}

static std::string json_escape_str(const std::string& s) {
    char* e = rex_json_escape(s.c_str());
    std::string out = e ? e : "";
    rex_free(e);
    return out;
}

static bool glob_match(const std::string& pat, const std::string& str) {
    // Simple wildcard match: * ? and ** (**) treated as *
    size_t p = 0, t = 0, star = std::string::npos, match = 0;
    while (t < str.size()) {
        if (p < pat.size() && (pat[p] == '?' || pat[p] == str[t])) { ++p; ++t; }
        else if (p < pat.size() && pat[p] == '*') {
            // collapse **
            while (p < pat.size() && pat[p] == '*') ++p;
            star = p; match = t;
        } else if (star != std::string::npos) {
            p = star; t = ++match;
        } else {
            return false;
        }
    }
    while (p < pat.size() && pat[p] == '*') ++p;
    return p == pat.size();
}

static bool glob_match_path(const std::string& pat, const std::string& path, const std::string& base) {
    std::string p = pat, s = path;
    for (auto& c : p) if (c == '\\') c = '/';
    for (auto& c : s) if (c == '\\') c = '/';
    if (glob_match(p, s) || glob_match(p, base)) return true;
    if (p.find("**") != std::string::npos) {
        std::string tail = p;
        auto pos = tail.rfind('/');
        if (pos != std::string::npos) tail = tail.substr(pos + 1);
        if (glob_match(tail, base)) return true;
    }
    return false;
}


extern "C" char* rex_grep(const char* root, const char* pattern, const char* include_glob, const char* exclude_glob, int max_matches) {
    std::string rroot = root ? root : ".";
    std::string rpat = pattern ? pattern : "";
    std::string rinc = include_glob ? include_glob : "";
    std::string rexc = exclude_glob ? exclude_glob : "";
    if (max_matches <= 0) max_matches = 200;

    std::regex rx;
    try {
        rx = std::regex(rpat);
    } catch (...) {
        // Let the Python layer fall back to `re` for patterns ECMAScript rejects.
        return nullptr;
    }

    std::string out = "[";
    int count = 0;
    std::error_code ec;
    fs::recursive_directory_iterator it(rroot, fs::directory_options::skip_permission_denied, ec);
    fs::recursive_directory_iterator end;
    for (; it != end && count < max_matches; it.increment(ec)) {
        if (ec) { ec.clear(); continue; }
        if (!it->is_regular_file(ec)) { if (ec) ec.clear(); continue; }
        auto path = it->path().string();
        auto fn = it->path().filename().string();
        if (!rinc.empty() && !glob_match(rinc, fn)) continue;
        if (!rexc.empty() && glob_match(rexc, fn)) continue;
        std::ifstream f(path);
        if (!f) continue;
        std::string line;
        int lineno = 0;
        while (std::getline(f, line) && count < max_matches) {
            ++lineno;
            try {
                if (std::regex_search(line, rx)) {
                    if (count > 0) out += ",";
                    std::string text = line;
                    if (text.size() > 300) text = text.substr(0, 300);
                    out += "{\"path\":\"" + json_escape_str(path) + "\",\"line\":" + std::to_string(lineno) + ",\"text\":\"" + json_escape_str(text) + "\"}";
                    ++count;
                }
            } catch (...) { }
        }
    }
    out += "]";
    return dup_cstr(out);
}

extern "C" char* rex_glob(const char* root, const char* pattern) {
    std::string rroot = root ? root : ".";
    std::string rpat = pattern ? pattern : "*";
    std::vector<std::string> matches;
    std::error_code ec;

    // Support simple "**" recursive and normal fnmatch on relative path.
    bool recursive = rpat.find("**") != std::string::npos;
    auto match_path = [&](const fs::path& p) {
        std::string rel = fs::relative(p, rroot, ec).string();
        if (ec) { ec.clear(); rel = p.string(); }
        std::string base = p.filename().string();
        return glob_match_path(rpat, rel, base);
    };

    if (recursive) {
        fs::recursive_directory_iterator it(rroot, fs::directory_options::skip_permission_denied, ec);
        fs::recursive_directory_iterator end;
        for (; it != end; it.increment(ec)) {
            if (ec) { ec.clear(); continue; }
            if (match_path(it->path())) matches.push_back(it->path().string());
        }
    } else {
        fs::directory_iterator it(rroot, fs::directory_options::skip_permission_denied, ec);
        fs::directory_iterator end;
        for (; it != end; it.increment(ec)) {
            if (ec) { ec.clear(); continue; }
            if (match_path(it->path())) matches.push_back(it->path().string());
        }
    }
    std::sort(matches.begin(), matches.end());
    std::string out = "[";
    for (size_t i = 0; i < matches.size(); ++i) {
        if (i) out += ",";
        out += "\"" + json_escape_str(matches[i]) + "\"";
    }
    out += "]";
    return dup_cstr(out);
}
