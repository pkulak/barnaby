package main

import (
	"slices"
	"strings"
)

// shortIDLen is the shortest prefix shown for a Matrix event or room ID,
// counting the sigil: "$6b5O2_" or "!Lmhiri". Full IDs cost the agent about
// 30 and 15 tokens each, on every message.
const shortIDLen = 7

// shortID abbreviates id the way git abbreviates hashes: shortIDLen
// characters, or more if that prefix would also match another known ID. An
// ID that isn't known is returned whole, so every short ID resolves.
func shortID(id string, known []string) string {
	if !slices.Contains(known, id) {
		return id
	}

	for n := shortIDLen; n < len(id); n++ {
		prefix := id[:n]
		unique := true

		for _, other := range known {
			if other != id && strings.HasPrefix(other, prefix) {
				unique = false

				break
			}
		}

		if unique {
			return prefix
		}
	}

	return id
}

// resolveID returns the known ID that equals prefix, or else the only known
// ID that starts with it. It returns "" when none or several match.
func resolveID(prefix string, known []string) string {
	if prefix == "" {
		return ""
	}

	for _, id := range known {
		if id == prefix {
			return id
		}
	}

	match := ""

	for _, id := range known {
		if strings.HasPrefix(id, prefix) {
			if match != "" {
				return ""
			}

			match = id
		}
	}

	return match
}
