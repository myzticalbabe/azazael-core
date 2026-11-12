// judge_go — swarm-arena shard (Go): structure-specificity pairwise judge.
// Reads pairs JSON from stdin: [{"pair":"...","x":"...","y":"..."}]
// Writes verdicts JSON to stdout: [{"pair":"...","winner":"X|Y","sx":f,"sy":f}]
// Stdlib only, deterministic. Scores investigative structure, not vocabulary:
// section headers, identifiers (NPI/EIN/licenses), dollar specificity,
// parenthetical detail, dates. Deliberately different signals from judge_rs
// (evidence refs) and judge_py (cross-reference density) so the three
// shards disagree like independent arena voters instead of echoing.
package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"strings"
)

type Pair struct {
	Pair string `json:"pair"`
	X    string `json:"x"`
	Y    string `json:"y"`
}

type Verdict struct {
	Pair   string  `json:"pair"`
	Winner string  `json:"winner"`
	Sx     float64 `json:"sx"`
	Sy     float64 `json:"sy"`
}

func min3(a, b int) int {
	if a < b {
		return a
	}
	return b
}

func countSub(hay, sub string) int { return strings.Count(hay, sub) }

func score(t string) float64 {
	s := 0.0
	// Investigative structure headers
	for _, h := range []string{"**What**", "**Why**", "**Where**", "**Learned**"} {
		if strings.Contains(t, h) {
			s += 2.0
		}
	}
	// Hard identifiers: NPI (10 digits), EIN (NN-NNNNNNN), licenses
	s += float64(min3(countSub(t, "NPI "), 4)) * 2.0
	s += float64(min3(countSub(t, "EIN "), 2)) * 2.0
	for _, lic := range []string{"license", "License", "certified", "charter", "Docket"} {
		if strings.Contains(t, lic) {
			s += 1.0
			break
		}
	}
	// Dollar specificity
	s += float64(min3(countSub(t, "$"), 3)) * 1.0
	// Parenthetical detail density
	s += float64(min3(countSub(t, " ("), 6)) * 0.5
	// Dates anchor recency
	s += float64(min3(countSub(t, "2026-"), 2)) * 1.0
	// Named-entity proxy: comma-separated role appositions ("CEO, 68,")
	s += float64(min3(countSub(t, ", "), 8)) * 0.25
	return s
}

func main() {
	raw, err := io.ReadAll(os.Stdin)
	if err != nil {
		fmt.Fprintln(os.Stderr, "read:", err)
		os.Exit(1)
	}
	var pairs []Pair
	if err := json.Unmarshal(raw, &pairs); err != nil {
		fmt.Fprintln(os.Stderr, "parse:", err)
		os.Exit(1)
	}
	out := make([]Verdict, 0, len(pairs))
	for _, p := range pairs {
		sx, sy := score(p.X), score(p.Y)
		w := "X"
		switch {
		case sy > sx:
			w = "Y"
		case sx == sy && len(p.Y) > len(p.X):
			w = "Y"
		}
		out = append(out, Verdict{Pair: p.Pair, Winner: w, Sx: sx, Sy: sy})
	}
	enc, _ := json.Marshal(out)
	fmt.Println(string(enc))
}
