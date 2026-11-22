CXX := g++
CXXFLAGS := -std=c++17 -O2 -Wall -Wextra -fPIC
LDFLAGS := -pthread

SO := ../runtimelib/native/librex_native.so
TEST := ./test_native

all: $(SO) $(TEST)

$(SO): rex_native.cpp rex_native.h
	$(CXX) $(CXXFLAGS) -shared -o $@ rex_native.cpp $(LDFLAGS)

$(TEST): test_native.cpp rex_native.cpp rex_native.h
	$(CXX) $(CXXFLAGS) -o $@ test_native.cpp rex_native.cpp $(LDFLAGS)

test: $(TEST)
	$(TEST)

clean:
	rm -f $(SO) $(TEST)

.PHONY: all test clean
