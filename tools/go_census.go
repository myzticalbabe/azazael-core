// go_census.go — Go-based concurrent codebase census.
// Compiled: go build -o go_census.exe go_census.go
// Usage: go_census.exe --root src/ --json
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"time"
)

type FileStats struct {
	Path      string `json:"path"`
	Lines     int    `json:"lines"`
	Functions int    `json:"functions"`
	Classes   int    `json:"classes"`
	Imports   int    `json:"imports"`
	Language  string `json:"language"`
}

type CensusResult struct {
	TotalFiles    int         `json:"total_files"`
	TotalLines    int         `json:"total_lines"`
	TotalFuncs    int         `json:"total_functions"`
	TotalClasses  int         `json:"total_classes"`
	TotalImports  int         `json:"total_imports"`
	ByLanguage    map[string]int `json:"by_language"`
	Files         []FileStats `json:"files"`
	Workers       int         `json:"workers"`
	ElapsedMs     int64       `json:"elapsed_ms"`
}

var extLang = map[string]string{
	".py": "python", ".js": "javascript", ".ts": "typescript",
	".html": "html", ".ps1": "powershell", ".sh": "bash",
	".bat": "batch", ".go": "go", ".rs": "rust",
	".md": "markdown", ".json": "json", ".yaml": "yaml",
	".yml": "yaml", ".toml": "toml",
}

func countStats(content string) (int, int, int, int) {
	lines := 0
	funcs := 0
	classes := 0
	imports := 0
	for _, line := range strings.Split(content, "\n") {
		lines++
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, "def ") || strings.HasPrefix(trimmed, "async def ") || strings.HasPrefix(trimmed, "func ") {
			funcs++
		}
		if strings.HasPrefix(trimmed, "class ") || strings.HasPrefix(trimmed, "type ") {
			classes++
		}
		if strings.HasPrefix(trimmed, "import ") || strings.HasPrefix(trimmed, "from ") || strings.HasPrefix(trimmed, "require(") {
			imports++
		}
	}
	return lines, funcs, classes, imports
}

func main() {
	root := flag.String("root", "src", "root directory")
	jsonOut := flag.Bool("json", false, "JSON output")
	flag.Parse()

	start := time.Now()
	workers := runtime.NumCPU()

	// Collect files
	var files []string
	filepath.Walk(*root, func(path string, info os.FileInfo, err error) error {
		if err != nil || info.IsDir() {
			if info != nil && info.IsDir() && (info.Name() == "__pycache__" || info.Name() == "node_modules" || info.Name() == ".git") {
				return filepath.SkipDir
			}
			return nil
		}
		ext := strings.ToLower(filepath.Ext(path))
		if _, ok := extLang[ext]; ok {
			files = append(files, path)
		}
		return nil
	})

	// Process files concurrently
	results := make([]FileStats, len(files))
	var wg sync.WaitGroup
	sem := make(chan struct{}, workers)

	for i, f := range files {
		wg.Add(1)
		go func(idx int, path string) {
			defer wg.Done()
			sem <- struct{}{}
			defer func() { <-sem }()

			content, err := os.ReadFile(path)
			if err != nil {
				return
			}
			ext := strings.ToLower(filepath.Ext(path))
			lang := extLang[ext]
			lines, funcs, classes, imports := countStats(string(content))
			results[idx] = FileStats{
				Path:      path,
				Lines:     lines,
				Functions: funcs,
				Classes:   classes,
				Imports:   imports,
				Language:  lang,
			}
		}(i, f)
	}
	wg.Wait()

	// Aggregate
	census := CensusResult{
		TotalFiles:   len(files),
		ByLanguage:   make(map[string]int),
		Files:        results,
		Workers:      workers,
		ElapsedMs:    time.Since(start).Milliseconds(),
	}
	for _, r := range results {
		census.TotalLines += r.Lines
		census.TotalFuncs += r.Functions
		census.TotalClasses += r.Classes
		census.TotalImports += r.Imports
		census.ByLanguage[r.Language]++
	}

	if *jsonOut {
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		enc.Encode(census)
	} else {
		fmt.Printf("Go Census: %d files in %dms (%d workers)\n", census.TotalFiles, census.ElapsedMs, workers)
		fmt.Printf("  Lines: %d, Functions: %d, Classes: %d, Imports: %d\n",
			census.TotalLines, census.TotalFuncs, census.TotalClasses, census.TotalImports)
		for lang, count := range census.ByLanguage {
			fmt.Printf("  %s: %d files\n", lang, count)
		}
	}
}
