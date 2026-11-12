# coding: utf-8
"""
Framework Activator

Module for ml_platforms functionality.

Functions:
    - interactive_mode()
    - main()
    - load_q_table()
    - save_q_table()
    - build_doc_index()
    - map_framework_capabilities()
    - parse_question()
    - get_q_value()
    - select_framework()
    - find_relevant_docs()

Classes:
    - QuestionType
    - Framework
    - Answer
    - QState
    - MLFrameworkQA

Usage:
    >>> import framework_activator
    >>> framework_activator.interactive_mode()

Author: Auto-generated
Date: 2026-02-10
"""

# coding: utf-8
"""
ML Framework Q&A System with Reinforcement Learning
===================================================

Activates ML frameworks to answer questions through the hyperlinked
documentation structure. Uses Deep Learning Q-Learning for answer
optimization and reinforcement learning for continuous improvement.

Usage:
    python framework_activator.py --question "How do I convert PyTorch to ONNX?"
    python framework_activator.py --debug "CUDA out of memory error"
    python framework_activator.py --interactive
"""

import json
import argparse
from pathlib import Path
# from typing import Dict, List, Optional, Tuple  # removed — all names have native equivalents
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
from typing import List
import re
import random

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

BASE_DIR = Path(__file__).parent.parent
DOCS_DIR = BASE_DIR / "ml_platforms" / "docs"


class QuestionType(Enum):
    """Types of questions the system can handle."""

    HOW_TO = "how_to"
    DEBUG = "debug"
    CONFIGURATION = "configuration"
    BEST_PRACTICE = "best_practice"
    COMPARISON = "comparison"
    INSTALLATION = "installation"
    GENERAL = "general"
    CONVERT = "convert"


class Framework(Enum):
    """ML Frameworks supported by the system."""

    PYTORCH = "pytorch"
    TENSORFLOW = "tensorflow"
    SKLEARN = "sklearn"
    JAX = "jax"
    ONNX = "onnx"
    RAY = "ray"
    MLFLOW = "mlflow"
    NUMPY = "numpy"
    TRANSFORMERS = "transformers"
    CV2 = "opencv"
    UNKNOWN = "unknown"


@dataclass
class Answer:
    """Structured answer from the system."""

    content: str
    confidence: float
    framework: Framework
    source_doc: str
    related_docs: list[str] = field(default_factory=list)
    code_example: (str | None) = None
    debugging_tips: list[str] = field(default_factory=list)
    verification_steps: list[str] = field(default_factory=list)
    q_value: float = 0.0


@dataclass
class QState:
    """State representation for Q-learning."""

    question_type: QuestionType
    primary_framework: Framework
    keywords: list[str]
    complexity: int  # 1-5 scale


class MLFrameworkQA:
    """
    Main Q&A System that activates frameworks and uses Q-Learning
    for answer optimization.
    """

    def __init__(self):
        self.q_table: dict[tuple[str, str], float] = {}
        self.framework_capabilities: dict[Framework, list[str]] = {}
        self.doc_index: dict[str, dict] = {}
        self.question_history: list[dict] = []
        self.load_q_table()
        self.build_doc_index()
        self.map_framework_capabilities()

    def load_q_table(self):
        """Load Q-table from file or initialize."""
        q_table_path = BASE_DIR / "ml_platforms" / "analysis_logs" / "qa_q_table.json"
        if q_table_path.exists():
            with open(q_table_path, encoding="utf-8") as f:
                self.q_table = json.load(f)
        else:
            self._initialize_q_table()

    def _initialize_q_table(self):
        """Initialize Q-table with default values."""
        for qt in QuestionType:
            for fw in Framework:
                self.q_table[(qt.value, fw.value)] = 0.5  # Initial confidence

    def save_q_table(self):
        """Persist Q-table to disk."""
        q_table_path = BASE_DIR / "ml_platforms" / "analysis_logs" / "qa_q_table.json"
        q_table_path.parent.mkdir(exist_ok=True)
        with open(q_table_path, "w", encoding="utf-8") as f:
            json.dump(self.q_table, f, indent=2)

    def build_doc_index(self):
        """Build index of all documentation files."""
        if not DOCS_DIR.exists():
            return

        for md_file in DOCS_DIR.rglob("*.md"):
            if "INDEX" in md_file.name:
                continue

            relative_path = str(md_file.relative_to(BASE_DIR))
            with open(md_file, "r", encoding="utf-8") as f:
                content = f.read()

            # Extract keywords from content
            keywords = self._extract_keywords(content)

            # Find related frameworks
            frameworks = self._detect_frameworks(content)

            self.doc_index[md_file.stem] = {
                "path": relative_path,
                "content": content,
                "keywords": keywords,
                "frameworks": frameworks,
                "title": self._extract_title(content),
            }

    def _extract_keywords(self, content: str) -> list[str]:
        """Extract meaningful keywords from documentation."""

        # Extract code blocks
        code_pattern = r"```[\s\S]*?```"
        code_blocks = re.findall(code_pattern, content)

        # Extract function/class names
        pattern = r"(?:def|class)\s+(\w+)"
        re.findall(pattern, content)

        keywords = []
        for block in code_blocks:
            keywords.extend(re.findall(pattern, block))

        # Remove duplicates
        return list(set(keywords))

    def _detect_frameworks(self, content: str) -> list[Framework]:
        """Detect which frameworks are mentioned."""
        detected = []

        framework_keywords = {
            Framework.PYTORCH: ["pytorch", "torch", "cuda"],
            Framework.TENSORFLOW: ["tensorflow", "tf", "keras"],
            Framework.SKLEARN: ["sklearn", "scikit-learn", "RandomForest"],
            Framework.JAX: ["jax", "jnp", "vmap"],
            Framework.ONNX: ["onnx", "onnxruntime"],
            Framework.RAY: ["ray", "remote"],
            Framework.MLFLOW: ["mlflow", "experiment"],
            Framework.NUMPY: ["numpy", "np"],
            Framework.TRANSFORMERS: ["transformers", "huggingface"],
            Framework.CV2: ["opencv", "cv2", "imread"],
        }

        content_lower = content.lower()
        for fw, keywords in framework_keywords.items():
            if any(kw in content_lower for kw in keywords):
                detected.append(fw)

        return detected

    def _extract_title(self, content: str) -> str:
        """Extract title from markdown."""
        for line in content.split("\n"):
            if line.startswith("# "):
                return line[2:].strip()
        return "Untitled"

    def map_framework_capabilities(self):
        """Map what each framework can do."""
        self.framework_capabilities = {
            Framework.PYTORCH: [
                "tensor_operations",
                "neural_networks",
                "cuda_acceleration",
                "autograd",
                "distributed_training",
                "torchscript",
            ],
            Framework.TENSORFLOW: [
                "tensor_operations",
                "keras_api",
                "serving",
                "tensorboard",
                "tf_data",
                "tf_function",
            ],
            Framework.SKLEARN: [
                "classification",
                "regression",
                "clustering",
                "dimensionality_reduction",
                "model_selection",
                "pipelines",
            ],
            Framework.JAX: ["vmap", "grad", "jit", "pmap", "functional_programming"],
            Framework.ONNX: [
                "model_export",
                "model_optimization",
                "cross_platform_inference",
            ],
            Framework.RAY: [
                "distributed_computing",
                "hyperparameter_tuning",
                "actor_model",
            ],
            Framework.MLFLOW: ["experiment_tracking", "model_registry", "deployment"],
            Framework.NUMPY: ["array_operations", "linear_algebra", "random_numbers"],
            Framework.TRANSFORMERS: [
                "nlp_models",
                "tokenization",
                "fine_tuning",
                "pipelines",
            ],
            Framework.CV2: ["image_processing", "object_detection", "video_analysis"],
        }

    def parse_question(self, question: str) -> QState:
        """Parse a question into a Q-state."""
        question_lower = question.lower()

        # Detect question type
        if "how" in question_lower or "?" in question:
            if (
                "debug" in question_lower
                or "error" in question_lower
                or "fail" in question_lower
            ):
                q_type = QuestionType.DEBUG
            elif "install" in question_lower:
                q_type = QuestionType.INSTALLATION
            elif "compare" in question_lower or "difference" in question_lower:
                q_type = QuestionType.COMPARISON
            elif "best" in question_lower or "should" in question_lower:
                q_type = QuestionType.BEST_PRACTICE
            elif "config" in question_lower or "set" in question_lower:
                q_type = QuestionType.CONFIGURATION
            else:
                q_type = QuestionType.HOW_TO
        else:
            q_type = QuestionType.GENERAL

        # Detect primary framework
        primary_framework = self._detect_primary_framework(question)

        # Extract keywords
        keywords = self._extract_question_keywords(question)

        # Estimate complexity
        complexity = self._estimate_complexity(question)

        return QState(
            question_type=q_type,
            primary_framework=primary_framework,
            keywords=keywords,
            complexity=complexity,
        )

    def _detect_primary_framework(self, question: str) -> Framework:
        """Detect the main framework in the question."""
        question_lower = question.lower()

        framework_indicators = {
            Framework.PYTORCH: ["pytorch", "torch", "nn.module", "autograd"],
            Framework.TENSORFLOW: ["tensorflow", "tf.", "keras", "tensorboard"],
            Framework.SKLEARN: ["sklearn", "scikit-learn", "RandomForest", "SVC"],
            Framework.JAX: ["jax", "jnp.", "vmap", "grad"],
            Framework.ONNX: ["onnx", "onnxruntime", "export"],
            Framework.RAY: ["ray", "remote", "distributed"],
            Framework.MLFLOW: ["mlflow", "tracking", "experiment"],
            Framework.NUMPY: ["numpy", "np.", "array"],
            Framework.TRANSFORMERS: ["transformer", "huggingface", "bert", "gpt"],
            Framework.CV2: ["opencv", "cv2", "imread", "imwrite"],
        }

        scores: dict[Framework, int] = {}
        for fw, indicators in framework_indicators.items():
            score = sum(1 for ind in indicators if ind in question_lower)
            if score > 0:
                scores[fw] = score

        if scores:
            return max(scores, key=lambda k: scores[k])
        return Framework.UNKNOWN

    def _extract_question_keywords(self, question: str) -> list[str]:
        """Extract key terms from question."""

        # Common ML terms
        ml_terms = [
            "train",
            "predict",
            "model",
            "tensor",
            "gradient",
            "loss",
            "accuracy",
            "batch",
            "epoch",
            "layer",
            "optimizer",
            "dataset",
            "dataloader",
            "checkpoint",
            "convert",
            "export",
            "deploy",
            "debug",
            "optimize",
            "cluster",
            "parallel",
            "distributed",
            "hyperparameter",
        ]

        question_lower = question.lower()
        keywords = [term for term in ml_terms if term in question_lower]

        # Extract any code-like terms
        code_terms = re.findall(r"`([^`]+)`|(\w+)", question)
        for match in code_terms:
            if match[0]:
                keywords.append(match[0])
            elif match[1] and len(match[1]) > 3:
                keywords.append(match[1])

        return list(set(keywords))

    def _estimate_complexity(self, question: str) -> int:
        """Estimate question complexity (1-5)."""
        complexity = 1

        # Multi-part questions
        if " and " in question or " or " in question:
            complexity += 1

        # Advanced terms
        advanced_terms = [
            "distributed",
            "custom",
            "debugging",
            "optimization",
            "deployment",
            "production",
            "multi-gpu",
            "mixed-precision",
        ]
        for term in advanced_terms:
            if term in question.lower():
                complexity += 1

        return min(complexity, 5)

    def get_q_value(self, state: QState) -> dict[Framework, float]:
        """Get Q-values for each framework given the state."""
        q_values = {}

        for fw in Framework:
            key = (state.question_type.value, fw.value)
            base_q = self.q_table.get(key, 0.5)

            # Boost if framework matches question
            boost = 0.3 if fw == state.primary_framework else 0.0

            # Adjust by complexity
            complexity_factor = (5 - state.complexity) * 0.02

            q_values[fw] = min(1.0, base_q + boost + complexity_factor)

        return q_values

    def select_framework(self, state: QState) -> tuple[Framework, float]:
        """Select best framework using epsilon-greedy strategy."""

        epsilon = 0.1  # Exploration rate

        if random.random() < epsilon:
            # Explore: random framework
            return random.choice(list(Framework)), 0.5

        # Exploit: best Q-value
        q_values = self.get_q_value(state)

        # Filter out UNKNOWN if we have alternatives
        candidates = [(fw, q) for fw, q in q_values.items() if fw != Framework.UNKNOWN]

        if not candidates:
            candidates = list(q_values.items())

        best_fw, best_q = max(candidates, key=lambda x: x[1])
        return best_fw, best_q

    def find_relevant_docs(self, state: QState) -> list[dict]:
        """Find relevant documentation based on question."""
        relevant = []

        for doc_name, doc_info in self.doc_index.items():
            score = 0.0

            # Framework match
            if state.primary_framework in doc_info.get("frameworks", []):
                score += 0.5

            # Keyword overlap
            doc_keywords = set(doc_info.get("keywords", []))
            question_keywords = set(state.keywords)
            overlap = len(doc_keywords & question_keywords)
            score += overlap * 0.1

            # Question type match
            if state.question_type.value.replace("_", "-") in doc_name.lower():
                score += 0.2

            if score > 0.1:
                relevant.append({"doc": doc_info, "score": score})

        # Sort by score
        relevant.sort(key=lambda x: x["score"], reverse=True)

        return [r["doc"] for r in relevant[:5]]

    def generate_answer(
        self, question: str, state: QState, framework: Framework
    ) -> Answer:
        """Generate a comprehensive answer."""

        # Find relevant docs
        docs = self.find_relevant_docs(state)

        # Get Q-value for confidence
        q_values = self.get_q_value(state)
        q_value = q_values.get(framework, 0.5)

        # Build answer content
        content_parts = []

        # Framework introduction
        fw_name = framework.value.upper()
        content_parts.append(f"# {fw_name} Solution\n")

        if framework != Framework.UNKNOWN:
            capabilities = self.framework_capabilities.get(framework, [])
            content_parts.append("## Capabilities")
            for cap in capabilities[:5]:
                content_parts.append(f"- {cap.replace('_', ' ').title()}")

        # Solution from docs
        content_parts.append("\n## Solution\n")

        if docs:
            best_doc = docs[0]
            solution = self._extract_solution(best_doc["content"], state)
            content_parts.append(solution)

            related = [d["path"] for d in docs[1:4]]
        else:
            content_parts.append("Based on your question, here's the approach:\n")
            content_parts.append(self._generate_generic_solution(state))
            related = []

        # Generate code example
        code_example = self._generate_code_example(state, framework)
        if code_example:
            content_parts.append(f"\n## Code Example\n\n```python\n{code_example}\n```")

        # Debugging tips (if question type is DEBUG)
        debug_tips = []
        if state.question_type == QuestionType.DEBUG:
            debug_tips = self._generate_debugging_tips(state, framework)
            if debug_tips:
                content_parts.append("\n## Debugging Tips\n")
                for tip in debug_tips:
                    content_parts.append(f"- {tip}")

        # Verification steps
        verification = self._generate_verification_steps(state, framework)
        if verification:
            content_parts.append("\n## Verification\n")
            for step in verification:
                content_parts.append(f"- {step}")

        return Answer(
            content="\n".join(content_parts),
            confidence=q_value,
            framework=framework,
            source_doc=docs[0]["path"] if docs else "system",
            related_docs=related,
            code_example=code_example,
            debugging_tips=debug_tips,
            verification_steps=verification,
            q_value=q_value,
        )

    def _extract_solution(self, content: str, state: QState) -> str:
        """Extract relevant solution from documentation."""
        lines = content.split("\n")

        # Find sections relevant to question type
        relevant_lines = []
        skip_code = True

        for i, line in enumerate(lines):
            # Skip code blocks
            if line.strip().startswith("```"):
                skip_code = not skip_code
                continue
            if skip_code:
                continue

            # Look for relevant headers
            if line.startswith("#"):
                # Reset if we've moved past relevant section
                if state.question_type.value.replace("_", " ") in line.lower():
                    relevant_lines = []
                continue

            # Add content under relevant headers
            if relevant_lines and not line.startswith("#"):
                if len(line.strip()) > 10:
                    relevant_lines.append(line)

        if relevant_lines[:20]:
            return "\n".join(relevant_lines[:20])

        # Fallback: return first substantial paragraph
        for line in lines:
            if len(line.strip()) > 50 and not line.startswith("#"):
                return line.strip()

        return "See the documentation for detailed information."

    def _generate_generic_solution(self, state: QState) -> str:
        """Generate a generic solution when no docs found."""
        solutions = {
            QuestionType.HOW_TO: "Here's how to approach this task:\n"
            "1. Import the required framework\n"
            "2. Prepare your data\n"
            "3. Build your model/solution\n"
            "4. Execute the operation\n"
            "5. Verify the results",
            QuestionType.DEBUG: "Debugging steps:\n"
            "1. Check error message details\n"
            "2. Verify input data format\n"
            "3. Review framework compatibility\n"
            "4. Test with minimal example\n"
            "5. Consult framework documentation",
            QuestionType.INSTALLATION: "Installation steps:\n"
            "1. Check Python version requirements\n"
            "2. Install via pip or conda\n"
            "3. Verify installation\n"
            "4. Test imports\n"
            "5. Check CUDA compatibility if GPU needed",
            QuestionType.BEST_PRACTICE: "Best practices:\n"
            "1. Follow framework conventions\n"
            "2. Use appropriate data structures\n"
            "3. Implement proper error handling\n"
            "4. Document your code\n"
            "5. Test thoroughly",
            QuestionType.COMPARISON: "Framework comparison:\n"
            "- Consider task requirements\n"
            "- Evaluate performance needs\n"
            "- Check community support\n"
            "- Assess learning curve\n"
            "- Review deployment options",
            QuestionType.CONFIGURATION: "Configuration steps:\n"
            "1. set up environment variables\n"
            "2. Configure framework settings\n"
            "3. Initialize components\n"
            "4. Test configuration\n"
            "5. Optimize settings",
            QuestionType.GENERAL: "General guidance:\n"
            "1. Identify your requirements\n"
            "2. Select appropriate framework\n"
            "3. Follow framework patterns\n"
            "4. Test thoroughly\n"
            "5. Document your solution",
        }

        return solutions.get(state.question_type, solutions[QuestionType.GENERAL])

    def _generate_code_example(
        self, state: QState, framework: Framework
    ) -> (str | None):
        """Generate a code example based on question and framework."""
        examples = {
            (QuestionType.HOW_TO, Framework.PYTORCH): """
import torch
import torch.nn as nn

# Define model
model = nn.Sequential(
    nn.Linear(784, 128),
    nn.ReLU(),
    nn.Linear(128, 10)
)

# Forward pass
x = torch.randn(1, 784)
output = model(x)
print(f"Output shape: {output.shape}")
""",
            (QuestionType.HOW_TO, Framework.TENSORFLOW): """
import tensorflow as tf

# Define model
model = tf.keras.Sequential([
    tf.keras.layers.Dense(128, activation='relu', input_shape=(784,)),
    tf.keras.layers.Dense(10, activation='softmax')
])

# Forward pass
x = tf.random.normal([1, 784])
output = model(x)
print(f"Output shape: {output.shape}")
""",
            (QuestionType.DEBUG, Framework.PYTORCH): """
# Debug CUDA out of memory
import torch

# Clear cache
torch.cuda.empty_cache()

# Use smaller batch
batch_size = batch_size // 2

# Check GPU memory
print(f"GPU Memory: {torch.cuda.get_device_name(0)}")
print(f"Allocated: {torch.cuda.memory_allocated() / 1024**3:.2f}GB")
""",
            (QuestionType.CONVERT, Framework.PYTORCH): """
import torch

# Export to ONNX
dummy_input = torch.randn(1, 3, 224, 224)
torch.onnx.export(
    model,
    dummy_input,
    "model.onnx",
    input_names=['input'],
    output_names=['output']
)
print("Model exported successfully!")
""",
        }

        key = (state.question_type, framework)
        return examples.get(key)

    def _generate_debugging_tips(
        self, state: QState, framework: Framework
    ) -> list[str]:
        """Generate debugging tips for the question."""
        tips = {
            Framework.PYTORCH: [
                "Check tensor shapes with model(x).shape",
                "Verify CUDA availability with torch.cuda.is_available()",
                "Use torch.autograd.set_detect_anomaly(True) for gradient issues",
                "Clear cache with torch.cuda.empty_cache()",
            ],
            Framework.TENSORFLOW: [
                "Use tf.debugging.enable_check_numerics() for NaN detection",
                "Check model.summary() for layer details",
                "Verify input shape with model.input_shape",
                "Use tensorboard for visualization",
            ],
            Framework.SKLEARN: [
                "Check feature alignment between train and test",
                "Verify no data leakage in preprocessing",
                "Use cross-validation for reliable metrics",
                "Check class distribution for imbalanced data",
            ],
        }

        return tips.get(
            framework,
            [
                "Check framework documentation",
                "Verify input data format",
                "Test with minimal example",
                "Review error traceback for specifics",
            ],
        )

    def _generate_verification_steps(
        self, state: QState, framework: Framework
    ) -> list[str]:
        """Generate verification steps for the solution."""
        return [
            f"Verify {framework.value} is imported correctly",
            "Test with sample input data",
            "Check output shape and type",
            "Validate results against expected behavior",
            "Run unit tests if available",
        ]

    def process_question(
        self, question: str, feedback: (float | None) = None
    ) -> Answer:
        """Process a question and return an answer."""

        # Parse question into state
        state = self.parse_question(question)

        # Select best framework using Q-learning
        framework, q_val = self.select_framework(state)

        # Generate answer
        answer = self.generate_answer(question, state, framework)

        # Update Q-table based on feedback (if provided)
        if feedback is not None:
            self.update_q_table(state, framework, feedback)

        # Record in history
        self.question_history.append(
            {
                "timestamp": datetime.now().isoformat(),
                "question": question,
                "state": {
                    "question_type": state.question_type.value,
                    "framework": framework.value,
                    "keywords": state.keywords,
                    "complexity": state.complexity,
                },
                "answer": {
                    "confidence": answer.confidence,
                    "framework": answer.framework.value,
                    "source_doc": answer.source_doc,
                },
            }
        )

        return answer

    def update_q_table(self, state: QState, framework: Framework, reward: float):
        """Update Q-table based on user feedback (reinforcement learning)."""
        key = (state.question_type.value, framework.value)

        if key in self.q_table:
            # Q-learning update: Q(s,a) = Q(s,a) + alpha * (r - Q(s,a))
            alpha = 0.1
            self.q_table[key] = self.q_table[key] + alpha * (reward - self.q_table[key])
        else:
            self.q_table[key] = reward

        # Decay exploration over time
        for k in self.q_table:
            self.q_table[k] *= 0.999

        self.save_q_table()

    def format_answer(self, answer: Answer) -> str:
        """Format answer for display."""
        output = []

        output.append("=" * 70)
        output.append(f"FRAMEWORK: {answer.framework.value.upper()}")
        output.append(f"CONFIDENCE: {answer.confidence:.2%}")
        output.append(f"Q-VALUE: {answer.q_value:.4f}")
        output.append("=" * 70)
        output.append("")
        output.append(answer.content)
        output.append("")

        if answer.related_docs:
            output.append("-" * 70)
            output.append("RELATED DOCUMENTATION:")
            for doc in answer.related_docs:
                output.append(f"  * {doc}")

        output.append("")
        output.append("-" * 70)
        output.append("To improve answers, provide feedback:")
        output.append("  qa_system.process_question(question, rating)")
        output.append("  Where rating is 0.0-1.0 (your satisfaction)")

        return "\n".join(output)


def interactive_mode(qa_system: MLFrameworkQA):
    """Run interactive Q&A session."""
    print("\n" + "=" * 70)
    print("ML FRAMEWORK Q&A SYSTEM")
    print("Reinforcement Learning + Deep Learning Q-Learning")
    print("=" * 70)
    print("\nAsk questions like:")
    print('  "How do I export PyTorch model to ONNX?"')
    print('  "Debug: CUDA out of memory error"')
    print('  "Best practice for distributed training"')
    print("\nType 'quit' to exit.\n")

    while True:
        question = input("Question: ").strip()

        if question.lower() in ["quit", "exit", "q"]:
            print("\nGoodbye!")
            break

        if not question:
            continue

        # Process question
        answer = qa_system.process_question(question)

        # Display answer
        print(qa_system.format_answer(answer))

        # Optional feedback
        try:
            feedback = input("\nRate this answer (0.0-1.0) or press Enter: ").strip()
            if feedback:
                rating = float(feedback)
                if 0.0 <= rating <= 1.0:
                    # Update Q-table with feedback
                    state = qa_system.parse_question(question)
                    qa_system.update_q_table(state, answer.framework, rating)
                    print("Thank you for feedback! Learning...\n")
        except ValueError as exc:
            note_failure("framework_activator:L879", exc)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
    description="ML Framework Q&A System with Q-Learning"
    )
    parser.add_argument("--question", "-q", type=str, help="Question to answer")
    parser.add_argument("--debug", "-d", type=str, help="Debug a specific error")
    parser.add_argument(
    "--interactive", "-i", action="store_true", help="Enter interactive mode"
    )
    parser.add_argument(
    "--feedback",
    "-f",
    type=float,
    help="Provide feedback for last answer (0.0-1.0)",
    )

    args = parser.parse_args()

    # Initialize Q&A system
    qa_system = MLFrameworkQA()

    if args.interactive:
        interactive_mode(qa_system)
        return

        if args.question:
            question = args.question
    elif args.debug:
        question = f"Debug: {args.debug}"
    else:
        parser.print_help()
        return

        # Process question
        answer = qa_system.process_question(question)

        # Display formatted answer
        print(qa_system.format_answer(answer))

        # Handle feedback
        if args.feedback is not None:
            state = qa_system.parse_question(question)
            qa_system.update_q_table(state, answer.framework, args.feedback)
            print(f"\nQ-value updated: {answer.q_value:.4f}")


if __name__ == "__main__":
    main()
