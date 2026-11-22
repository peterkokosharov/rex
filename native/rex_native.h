#pragma once
#include <cstddef>

extern "C" {
    char* rex_json_escape(const char* s);
    char* rex_json_get_string(const char* json, const char* key);
    // Returns malloc'd payload string, malloc'd "[DONE]" for DONE, or NULL if not a data line.
    char* rex_parse_sse_data(const char* line);
    // Returns malloc'd JSON array string: [{"path":...,"line":N,"text":...},...]
    char* rex_grep(const char* root, const char* pattern, const char* include_glob, const char* exclude_glob, int max_matches);
    // Returns malloc'd JSON array string of paths.
    char* rex_glob(const char* root, const char* pattern);
    void rex_free(void* p);
}
