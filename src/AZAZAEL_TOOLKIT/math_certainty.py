#!/usr/bin/env python3
"""
MATHEMATICAL CERTAINTY CALCULATOR
Binary Comparison Analysis Tool
Implements multiple mathematical approaches to quantify binary similarity
"""

import hashlib
import math
import json
from pathlib import Path
from collections import Counter
from typing import List
import sys

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here
# from typing import Dict  # removed — all names have native equivalents


class BinaryComparator:
    """Mathematical binary comparison analyzer"""

    def __init__(self, file1: Path, file2: Path):
        self.file1 = file1
        self.file2 = file2
        self.data1 = file1.read_bytes() if file1.exists() else b""
        self.data2 = file2.read_bytes() if file2.exists() else b""

    def exact_match(self) -> dict:
        """Check if binaries are exactly identical"""
        is_match = self.data1 == self.data2
        return {
            "metric": "Exact Match",
            "result": is_match,
            "certainty": 1.0 if is_match else 0.0,
            "interpretation": "100% certain"
            if is_match
            else "0% - completely different",
        }

    def hamming_distance(self) -> dict:
        """Calculate bit-level Hamming distance"""
        len_min = min(len(self.data1), len(self.data2))
        len_max = max(len(self.data1), len(self.data2))

        # Count bit differences in overlapping region
        hamming = 0
        for i in range(len_min):
            diff = self.data1[i] ^ self.data2[i]
            hamming += bin(diff).count("1")

        total_bits = len_max * 8

        similarity = 1 - (hamming / total_bits) if total_bits > 0 else 1.0

        return {
            "metric": "Hamming Distance",
            "bit_differences": hamming,
            "total_bits": total_bits,
            "similarity": similarity,
            "certainty": similarity,
            "interpretation": f"{similarity * 100:.4f}% bit-level similarity",
        }

    def byte_similarity(self) -> dict:
        """Calculate byte-level similarity"""
        len1, len2 = len(self.data1), len(self.data2)
        min_len = min(len1, len2)

        matches = sum(1 for i in range(min_len) if self.data1[i] == self.data2[i])
        similarity = matches / max(len1, len2) if max(len1, len2) > 0 else 1.0

        return {
            "metric": "Byte Similarity",
            "matching_bytes": matches,
            "total_bytes": max(len1, len2),
            "similarity": similarity,
            "certainty": similarity,
            "interpretation": f"{similarity * 100:.2f}% byte-level similarity",
        }

    def jaccard_similarity(self) -> dict:
        """Calculate Jaccard similarity on byte values"""
        set1 = set(self.data1)
        set2 = set(self.data2)

        intersection = len(set1 & set2)
        union = len(set1 | set2)

        jaccard = intersection / union if union > 0 else 1.0

        return {
            "metric": "Jaccard Similarity",
            "unique_bytes_file1": len(set1),
            "unique_bytes_file2": len(set2),
            "intersection": intersection,
            "union": union,
            "similarity": jaccard,
            "certainty": jaccard,
            "interpretation": f"Jaccard index: {jaccard:.4f}",
        }

    def dice_coefficient(self) -> dict:
        """Calculate Dice coefficient"""
        set1 = set(self.data1)
        set2 = set(self.data2)

        intersection = len(set1 & set2)
        dice = (
            (2 * intersection) / (len(set1) + len(set2))
            if (len(set1) + len(set2)) > 0
            else 1.0
        )

        return {
            "metric": "Dice Coefficient",
            "intersection": intersection,
            "set1_size": len(set1),
            "set2_size": len(set2),
            "coefficient": dice,
            "certainty": dice,
            "interpretation": f"Dice: {dice:.4f}",
        }

    def longest_common_subsequence(self) -> dict:
        """Calculate normalized LCS length"""
        data1 = self.data1.decode("latin-1", errors="replace")
        data2 = self.data2.decode("latin-1", errors="replace")

        m, n = len(data1), len(data2)

        # Optimized LCS using DP with space optimization
        dp = [0] * (n + 1)
        for i in range(1, m + 1):
            prev = 0
            for j in range(1, n + 1):
                temp = dp[j]
                if data1[i - 1] == data2[j - 1]:
                    dp[j] = prev + 1
                else:
                    dp[j] = max(dp[j], dp[j - 1])
                prev = temp

        lcs_length = dp[n]
        normalized = (2 * lcs_length) / (m + n) if (m + n) > 0 else 1.0

        return {
            "metric": "Longest Common Subsequence",
            "lcs_length": lcs_length,
            "normalized_lcs": normalized,
            "length1": m,
            "length2": n,
            "similarity": normalized,
            "certainty": normalized,
            "interpretation": f"Normalized LCS: {normalized:.4f}",
        }

    def shannon_entropy(self) -> dict:
        """Calculate Shannon entropy"""

        def entropy(data):
            if len(data) == 0:
                return 0
            counts = Counter(data)
            ent = 0
            for count in counts.values():
                p = count / len(data)
                ent -= p * math.log2(p) if p > 0 else 0
            return ent

        ent1 = entropy(self.data1)
        ent2 = entropy(self.data2)

        # Entropy similarity (closer = more similar)
        entropy_diff = abs(ent1 - ent2)
        entropy_similarity = 1 - (entropy_diff / 8)  # Max entropy difference is 8

        return {
            "metric": "Shannon Entropy",
            "entropy_file1": ent1,
            "entropy_file2": ent2,
            "difference": entropy_diff,
            "similarity": entropy_similarity,
            "interpretation": f"Entropy: {ent1:.4f} vs {ent2:.4f}",
        }

    def rolling_hash_similarity(self) -> dict:
        """Compare using rolling hash"""

        k = 256  # k-gram size

        def kgram_hashes(data, k=k):
            if len(data) < k:
                return set()
            hashes = set()
            for i in range(len(data) - k + 1):
                segment = data[i : i + k]
                hashes.add(hashlib.md5(segment).digest())
            return hashes

        hashes1 = kgram_hashes(self.data1)
        hashes2 = kgram_hashes(self.data2)

        if not hashes1 or not hashes2:
            return {
                "metric": "Rolling Hash (k-gram)",
                "similarity": 0.0,
                "k_grams_file1": len(hashes1),
                "k_grams_file2": len(hashes2),
                "interpretation": "Files too small for k-gram analysis",
            }

        intersection = len(hashes1 & hashes2)
        union = len(hashes1 | hashes2)
        similarity = intersection / union if union > 0 else 0

        return {
            "metric": "Rolling Hash (k-gram, k=256)",
            "similarity": similarity,
            "k_grams_file1": len(hashes1),
            "k_grams_file2": len(hashes2),
            "intersection": intersection,
            "interpretation": f"k-gram similarity: {similarity:.4f}",
        }

    def structural_similarity(self) -> dict:
        """Calculate structural similarity based on byte patterns"""

        # Analyze byte run lengths
        def run_length_encoding(data):
            if len(data) == 0:
                return []
            runs = []
            current = data[0]
            count = 1
            for byte in data[1:]:
                if byte == current:
                    count += 1
                else:
                    runs.append((current, count))
                    current = byte
                    count = 1
            runs.append((current, count))
            return runs

        runs1 = run_length_encoding(self.data1)
        runs2 = run_length_encoding(self.data2)

        # Convert to sets of run patterns (normalized)
        patterns1 = set(r[1] for r in runs1)
        patterns2 = set(r[1] for r in runs2)

        if not patterns1 or not patterns2:
            return {
                "metric": "Structural Similarity",
                "similarity": 0.0,
                "interpretation": "No run patterns found",
            }

        intersection = len(patterns1 & patterns2)
        union = len(patterns1 | patterns2)
        similarity = intersection / union if union > 0 else 0

        return {
            "metric": "Structural Similarity",
            "run_patterns_file1": len(patterns1),
            "run_patterns_file2": len(patterns2),
            "similarity": similarity,
            "interpretation": f"Structure similarity: {similarity:.4f}",
        }

    def cryptographic_hash(self) -> dict:
        """Calculate cryptographic hashes"""
        hashes = {
            "md5_file1": hashlib.md5(self.data1).hexdigest(),
            "md5_file2": hashlib.md5(self.data2).hexdigest(),
            "sha256_file1": hashlib.sha256(self.data1).hexdigest(),
            "sha256_file2": hashlib.sha256(self.data2).hexdigest(),
        }

        hashes_match = hashes["md5_file1"] == hashes["md5_file2"]

        return {
            "metric": "Cryptographic Hash",
            **hashes,
            "exact_match": hashes_match,
            "certainty": 1.0 if hashes_match else 0.0,
            "interpretation": "MD5/SHA256 hash comparison",
        }

    def analyze(self) -> dict:
        """Run all analyses and return comprehensive report"""
        results = {
            "file1": str(self.file1),
            "file2": str(self.file2),
            "file1_size": len(self.data1),
            "file2_size": len(self.data2),
            "analyses": {},
        }

        analyses = [
            self.exact_match,
            self.cryptographic_hash,
            self.hamming_distance,
            self.byte_similarity,
            self.jaccard_similarity,
            self.dice_coefficient,
            self.longest_common_subsequence,
            self.shannon_entropy,
            self.rolling_hash_similarity,
            self.structural_similarity,
        ]

        similarities = []

        for analysis in analyses:
            result = analysis()
            results["analyses"][result["metric"]] = result
            if "certainty" in result:
                similarities.append(result["certainty"])

        # Calculate overall similarity
        results["overall_similarity"] = (
            sum(similarities) / len(similarities) if similarities else 0
        )
        results["overall_certainty"] = results["overall_similarity"]

        return results


def main():

    print("=" * 70)
    print("MATHEMATICAL CERTAINTY CALCULATOR - Binary Comparison")
    print("=" * 70)

    if len(sys.argv) < 3:
        print("\nUsage: python math_certainty.py <file1> <file2>")
        print("\nExample:")
        print("  python math_certainty.py file1.bin file2.bin")
        print("  python math_certainty.py image1.png image2.png")
        print()

        # Demo with sample data
        print("Running demo with sample binary data...")
        print()

        import numpy as np

        # Create sample binaries
        a = np.array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], dtype=np.uint8)
        b = np.array([1, 2, 3, 4, 5, 99, 99, 99, 99, 99], dtype=np.uint8)

        from tempfile import NamedTemporaryFile

        with (
        NamedTemporaryFile(delete=False) as f1,
        NamedTemporaryFile(delete=False) as f2,
        ):
            f1.write(a.tobytes())
            f2.write(b.tobytes())
            file1, file2 = Path(f1.name), Path(f2.name)

            try:
                comparator = BinaryComparator(file1, file2)
                results = comparator.analyze()

                print(f"File 1: {results['file1_size']} bytes")
                print(f"File 2: {results['file2_size']} bytes")
                print()

                for metric, data in results["analyses"].items():
                        print(f"\n{metric}:")
                        for key, value in data.items():
                            if key not in ["metric", "interpretation"]:
                                print(f"  {key}: {value}")

                print("\n" + "=" * 70)
                print(
                        f"OVERALL SIMILARITY: {results['overall_similarity']:.6f} ({results['overall_similarity'] * 100:.2f}%)"
                )
                print(
                        f"OVERALL CERTAINTY: {results['overall_certainty']:.6f} ({results['overall_certainty'] * 100:.2f}%)"
                )
                print("=" * 70)

            finally:
                file1.unlink()
                file2.unlink()

    else:
        file1 = Path(sys.argv[1])
        file2 = Path(sys.argv[2])

        if not file1.exists() or not file2.exists():
            print("Error: Files not found")
            sys.exit(1)

            comparator = BinaryComparator(file1, file2)
            results = comparator.analyze()

            print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
