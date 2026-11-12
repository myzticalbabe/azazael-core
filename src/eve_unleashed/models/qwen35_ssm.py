"""
Eve Unleashed — Qwen35 SSM Hybrid Model
=========================================
PyTorch implementation of the Qwen35ForCausalLM architecture from
eve_rebuild/config.json.

Hybrid design that interleaves State Space Model (SSM/Mamba) layers
with full self-attention every N layers for efficient long-context
inference (up to 262K tokens at 2560 hidden dim).

Architecture (32 layers):
  Layer 0-2:   RMSNorm → SSM → RMSNorm → FFN
  Layer 3:     RMSNorm → Attention → RMSNorm → FFN  (full_attention_interval=4)
  Layer 4-6:   RMSNorm → SSM → RMSNorm → FFN
  Layer 7:     RMSNorm → Attention → RMSNorm → FFN
  ...and so on.

SSM layer (Mamba-inspired):
  - Selective state space with state_size=128, inner_size=4096
  - 1D causal convolution with kernel=4
  - SiLU / swish activation
  - Grouped with group_count=16

Key specs:
  - hidden_size: 2560
  - num_hidden_layers: 32
  - num_attention_heads: 16
  - vocab_size: 248064
  - RMSNorm with eps=1e-6
  - RoPE with theta=10_000_000
  - max_position_embeddings: 262144
"""

import math
from dataclasses import dataclass
# from typing import Optional, Tuple  # removed — all names have native equivalents

import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import List
from pathlib import Path
import json

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


@dataclass
class Qwen35SSMConfig:
    """Configuration for Qwen35 SSM hybrid model."""
    vocab_size: int = 248064
    hidden_size: int = 2560
    intermediate_size: int = 9216
    num_hidden_layers: int = 32
    num_attention_heads: int = 16
    num_key_value_heads: int = 16
    max_position_embeddings: int = 262144
    rms_norm_eps: float = 1e-6
    rope_theta: float = 10000000.0
    torch_dtype: str = "float16"
    use_cache: bool = True
    bos_token_id: int = 248044
    eos_token_id: int = 248046

    # SSM-specific
    conv_kernel: int = 4
    group_count: int = 16
    inner_size: int = 4096
    state_size: int = 128
    time_step_rank: int = 32
    full_attention_interval: int = 4

    @classmethod
    def from_json(cls, path: str) -> "Qwen35SSMConfig":
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        ssm = data.get("ssm_config", {})
        return cls(
            vocab_size=data.get("vocab_size", 248064),
            hidden_size=data.get("hidden_size", 2560),
            intermediate_size=data.get("intermediate_size", 9216),
            num_hidden_layers=data.get("num_hidden_layers", 32),
            num_attention_heads=data.get("num_attention_heads", 16),
            num_key_value_heads=data.get("num_key_value_heads", 16),
            max_position_embeddings=data.get("max_position_embeddings", 262144),
            rms_norm_eps=data.get("rms_norm_eps", 1e-6),
            rope_theta=data.get("rope_theta", 10000000.0),
            torch_dtype=data.get("torch_dtype", "float16"),
            use_cache=data.get("use_cache", True),
            bos_token_id=data.get("bos_token_id", 248044),
            eos_token_id=data.get("eos_token_id", 248046),
            conv_kernel=ssm.get("conv_kernel", 4),
            group_count=ssm.get("group_count", 16),
            inner_size=ssm.get("inner_size", 4096),
            state_size=ssm.get("state_size", 128),
            time_step_rank=ssm.get("time_step_rank", 32),
            full_attention_interval=ssm.get("full_attention_interval", 4),
        )


# ── Core Layers ──────────────────────────────────────────────────────

class RMSNorm(nn.Module):
    """Root Mean Square Layer Normalization."""
    def __init__(self, hidden_size: int, eps: float = 1e-6):
        super().__init__()
        self.weight = nn.Parameter(torch.ones(hidden_size))
        self.eps = eps

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        dtype = x.dtype
        x = x.float()
        rms = torch.sqrt(x.pow(2).mean(-1, keepdim=True) + self.eps)
        return (x / rms * self.weight).to(dtype)


class RotaryEmbedding(nn.Module):
    """Rotary Position Embedding (RoPE)."""
    inv_freq: torch.Tensor  # buffer registered unconditionally in __init__

    def __init__(self, dim: int, max_position_embeddings: int = 262144, theta: float = 10000000.0):
        super().__init__()
        self.dim = dim
        self.max_position_embeddings = max_position_embeddings
        inv_freq = 1.0 / (theta ** (torch.arange(0, dim, 2).float() / dim))
        self.register_buffer("inv_freq", inv_freq, persistent=False)

    @torch.no_grad()
    def forward(self, x: torch.Tensor, position_ids: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        inv_freq_expanded = self.inv_freq[None, :, None].float().expand(position_ids.shape[0], -1, 1)
        position_ids_expanded = position_ids[:, None, :].float()
        freqs = (inv_freq_expanded @ position_ids_expanded).transpose(1, 2)
        emb = torch.cat((freqs, freqs), dim=-1)
        return emb.cos().to(x.dtype), emb.sin().to(x.dtype)


def rotate_half(x: torch.Tensor) -> torch.Tensor:
    x1, x2 = x[..., :x.shape[-1] // 2], x[..., x.shape[-1] // 2:]
    return torch.cat((-x2, x1), dim=-1)


def apply_rotary_pos_emb(q: torch.Tensor, k: torch.Tensor,
                         cos: torch.Tensor, sin: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    cos = cos.unsqueeze(1)
    sin = sin.unsqueeze(1)
    q_embed = (q * cos) + (rotate_half(q) * sin)
    k_embed = (k * cos) + (rotate_half(k) * sin)
    return q_embed, k_embed


# ── Attention Block ──────────────────────────────────────────────────

class Qwen35Attention(nn.Module):
    """Grouped Query Attention (GQA) with RoPE."""
    def __init__(self, config: Qwen35SSMConfig):
        super().__init__()
        self.hidden_size = config.hidden_size
        self.num_heads = config.num_attention_heads
        self.num_kv_heads = config.num_key_value_heads
        self.head_dim = config.hidden_size // config.num_attention_heads
        self.num_kv_groups = self.num_heads // self.num_kv_heads

        self.q_proj = nn.Linear(config.hidden_size, self.num_heads * self.head_dim, bias=False)
        self.k_proj = nn.Linear(config.hidden_size, self.num_kv_heads * self.head_dim, bias=False)
        self.v_proj = nn.Linear(config.hidden_size, self.num_kv_heads * self.head_dim, bias=False)
        self.o_proj = nn.Linear(self.num_heads * self.head_dim, config.hidden_size, bias=False)
        self.rotary = RotaryEmbedding(
            self.head_dim,
            config.max_position_embeddings,
            config.rope_theta,
        )

    def forward(self, hidden_states: torch.Tensor,
                position_ids: torch.Tensor,
                attention_mask: (torch.Tensor | None) = None) -> torch.Tensor:
        bsz, q_len, _ = hidden_states.size()

        q = self.q_proj(hidden_states).view(bsz, q_len, self.num_heads, self.head_dim).transpose(1, 2)
        k = self.k_proj(hidden_states).view(bsz, q_len, self.num_kv_heads, self.head_dim).transpose(1, 2)
        v = self.v_proj(hidden_states).view(bsz, q_len, self.num_kv_heads, self.head_dim).transpose(1, 2)

        cos, sin = self.rotary(hidden_states, position_ids)
        q, k = apply_rotary_pos_emb(q, k, cos, sin)

        # Expand KV heads for GQA
        if self.num_kv_groups > 1:
            k = k[:, :, None, :, :].expand(-1, -1, self.num_kv_groups, -1, -1).reshape(
                bsz, self.num_heads, q_len, self.head_dim)
            v = v[:, :, None, :, :].expand(-1, -1, self.num_kv_groups, -1, -1).reshape(
                bsz, self.num_heads, q_len, self.head_dim)

        # Causal attention mask
        if attention_mask is None:
            attention_mask = torch.triu(
                torch.full((q_len, q_len), float("-inf"), device=hidden_states.device),
                diagonal=1,
            )

        scale = 1.0 / math.sqrt(self.head_dim)
        attn_weights = torch.matmul(q, k.transpose(-2, -1)) * scale
        attn_weights = attn_weights + attention_mask
        attn_weights = F.softmax(attn_weights, dim=-1, dtype=torch.float32).to(q.dtype)
        attn_output = torch.matmul(attn_weights, v)

        attn_output = attn_output.transpose(1, 2).reshape(bsz, q_len, -1)
        return self.o_proj(attn_output)


# ── SSM (Mamba-inspired) Block ───────────────────────────────────────

class Qwen35SSM(nn.Module):
    """Selective State Space Model layer (Mamba-style).

    This is the core efficiency component — SSM layers replace full
    attention in 3 out of every 4 layers, reducing memory from O(n²)
    to O(n) for those layers.
    """
    def __init__(self, config: Qwen35SSMConfig):
        super().__init__()
        self.hidden_size = config.hidden_size
        self.inner_size = config.inner_size
        self.state_size = config.state_size
        self.group_count = config.group_count
        self.time_step_rank = config.time_step_rank
        self.conv_kernel = config.conv_kernel

        # Input projection: hidden → inner_size
        self.in_proj = nn.Linear(config.hidden_size, config.inner_size * 2, bias=False)

        # 1D causal convolution
        self.conv1d = nn.Conv1d(
            in_channels=config.inner_size,
            out_channels=config.inner_size,
            kernel_size=config.conv_kernel,
            groups=config.inner_size,  # depthwise
            padding=config.conv_kernel - 1,
            bias=False,
        )

        # Time-step projection
        self.dt_proj = nn.Linear(config.time_step_rank, config.inner_size, bias=False)
        self.dt_rank_proj = nn.Linear(config.inner_size, config.time_step_rank, bias=False)

        # SSM parameters (A, B, C, D)
        self.A_log = nn.Parameter(
            torch.log(torch.arange(1, config.state_size + 1, dtype=torch.float32)
                     .view(1, config.state_size, 1).repeat(config.inner_size, 1, 1))
        )
        self.B_proj = nn.Linear(config.inner_size, config.state_size, bias=False)
        self.C_proj = nn.Linear(config.inner_size, config.state_size, bias=False)
        self.D = nn.Parameter(torch.ones(config.inner_size))

        # Output projection: inner_size → hidden
        self.out_proj = nn.Linear(config.inner_size, config.hidden_size, bias=False)

    def _selective_scan(self, u: torch.Tensor, delta: torch.Tensor,
                        A: torch.Tensor, B: torch.Tensor, C: torch.Tensor,
                        D: torch.Tensor) -> torch.Tensor:
        """Parallel selective scan via cumprod/cumsum — vectorized over seq_len.

        Replaces the sequential Python for-loop with fully parallelized
        PyTorch operations (cumulative product + cumulative sum), giving
        O(log seq_len) parallel complexity instead of O(seq_len) serial.
        """
        bsz, seq_len, inner = u.shape

        # Discretize A and B
        delta = F.softplus(delta)
        # delta: (bsz, seq_len, inner)  →  (bsz, seq_len, inner, 1)
        # A: (inner, state_size, 1)  →  squeeze  →  (inner, state_size)  →  (1, 1, inner, state_size)
        A_2d = A.squeeze(-1)  # (inner, state_size)
        deltaA = torch.exp(delta.unsqueeze(-1) * A_2d.unsqueeze(0).unsqueeze(0))
        # deltaA: (bsz, seq_len, inner, state_size)

        # delta.unsqueeze(-1): (bsz, seq_len, inner, 1)
        # B.unsqueeze(2): (bsz, seq_len, 1, state_size)
        # u.unsqueeze(-1): (bsz, seq_len, inner, 1)
        deltaB_u = delta.unsqueeze(-1) * B.unsqueeze(2) * u.unsqueeze(-1)
        # deltaB_u: (bsz, seq_len, inner, state_size)

        # Parallel associative scan via cumprod/cumsum
        # The recurrence x[t] = deltaA[t] * x[t-1] + deltaB_u[t] unrolls to:
        #   x[t] = sum_{i=0}^{t} (prod_{j=i+1}^{t} deltaA[j]) * deltaB_u[i]
        # = cumprod(deltaA)[t] * cumsum(deltaB_u / cumprod(deltaA))[t]
        a_cumprod = torch.cumprod(deltaA, dim=1)  # (bsz, seq_len, inner, state_size)
        deltaB_u_scaled = deltaB_u / (a_cumprod + 1e-12)
        cumsum_scaled = torch.cumsum(deltaB_u_scaled, dim=1)
        x = a_cumprod * cumsum_scaled  # (bsz, seq_len, inner, state_size)

        # Compute outputs: y[t] = (x[t] * C[t]).sum(-1) + D * u[t]
        # C: (bsz, seq_len, state_size)  →  (bsz, seq_len, 1, state_size)
        y = (x * C.unsqueeze(2)).sum(-1) + D.unsqueeze(0).unsqueeze(0) * u
        # y: (bsz, seq_len, inner)
        return y

    def forward(self, hidden_states: torch.Tensor) -> torch.Tensor:
        bsz, seq_len, _ = hidden_states.shape

        # Project input
        proj = self.in_proj(hidden_states)
        x, z = proj.chunk(2, dim=-1)  # x: signal, z: gate

        # Causal 1D convolution
        x = x.transpose(1, 2)  # (bsz, inner, seq_len)
        x = self.conv1d(x)[:, :, :seq_len]  # trim padding
        x = F.silu(x)
        x = x.transpose(1, 2)  # (bsz, seq_len, inner)

        # Time-step delta
        dt_rank = self.dt_rank_proj(x)
        delta = self.dt_proj(dt_rank)

        # SSM parameters
        A = -torch.exp(self.A_log.float())  # (inner, state_size, 1)
        B = self.B_proj(x)  # (bsz, seq_len, state_size)
        C = self.C_proj(x)  # (bsz, seq_len, state_size)
        D = self.D

        # Selective scan
        y = self._selective_scan(x, delta, A, B, C, D)

        # Gate with z
        y = y * F.silu(z)

        return self.out_proj(y)


# ── Feed-Forward Network ─────────────────────────────────────────────

class Qwen35MLP(nn.Module):
    """SwiGLU Feed-Forward Network."""
    def __init__(self, config: Qwen35SSMConfig):
        super().__init__()
        self.gate_proj = nn.Linear(config.hidden_size, config.intermediate_size, bias=False)
        self.up_proj = nn.Linear(config.hidden_size, config.intermediate_size, bias=False)
        self.down_proj = nn.Linear(config.intermediate_size, config.hidden_size, bias=False)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        gate = F.silu(self.gate_proj(x))
        up = self.up_proj(x)
        return self.down_proj(gate * up)


# ── Decoder Layer ────────────────────────────────────────────────────

class Qwen35DecoderLayer(nn.Module):
    """Single decoder layer — either SSM or Attention based on position."""
    def __init__(self, config: Qwen35SSMConfig, layer_idx: int):
        super().__init__()
        self.layer_idx = layer_idx
        self.use_attention = (layer_idx % config.full_attention_interval == 0)

        self.input_norm = RMSNorm(config.hidden_size, config.rms_norm_eps)
        if self.use_attention:
            self.attn = Qwen35Attention(config)
        else:
            self.ssm = Qwen35SSM(config)

        self.post_attn_norm = RMSNorm(config.hidden_size, config.rms_norm_eps)
        self.mlp = Qwen35MLP(config)

    def forward(self, hidden_states: torch.Tensor,
                position_ids: torch.Tensor,
                attention_mask: (torch.Tensor | None) = None) -> torch.Tensor:
        residual = hidden_states
        hidden_states = self.input_norm(hidden_states)

        if self.use_attention:
            hidden_states = self.attn(hidden_states, position_ids, attention_mask)
        else:
            hidden_states = self.ssm(hidden_states)

        hidden_states = residual + hidden_states
        residual = hidden_states
        hidden_states = self.post_attn_norm(hidden_states)
        hidden_states = self.mlp(hidden_states)
        return residual + hidden_states


# ── Full Model ───────────────────────────────────────────────────────

class Qwen35ForCausalLM(nn.Module):
    """Qwen35 SSM-Hybrid Causal Language Model.

    32-layer transformer where 3 out of every 4 layers use efficient
    SSM (Mamba-style) instead of full attention. Designed for local
    inference at 2560 hidden dim — fits in 4-8 GB VRAM at fp16.

    Usage:
        config = Qwen35SSMConfig.from_json("eve_rebuild/config.json")
        model = Qwen35ForCausalLM(config)
        output = model.generate(input_ids, max_new_tokens=128)
    """

    def __init__(self, config: Qwen35SSMConfig):
        super().__init__()
        self.config = config

        self.embed_tokens = nn.Embedding(config.vocab_size, config.hidden_size)
        self.layers = nn.ModuleList([
            Qwen35DecoderLayer(config, i) for i in range(config.num_hidden_layers)
        ])
        self.norm = RMSNorm(config.hidden_size, config.rms_norm_eps)
        self.lm_head = nn.Linear(config.hidden_size, config.vocab_size, bias=False)

        # Tie weights
        self.lm_head.weight = self.embed_tokens.weight

    def forward(self, input_ids: torch.Tensor,
                attention_mask: (torch.Tensor | None) = None) -> torch.Tensor:
        """Forward pass returning logits."""
        bsz, seq_len = input_ids.shape
        position_ids = torch.arange(seq_len, device=input_ids.device).unsqueeze(0).expand(bsz, -1)

        hidden_states = self.embed_tokens(input_ids)
        for layer in self.layers:
            hidden_states = layer(hidden_states, position_ids, attention_mask)
        hidden_states = self.norm(hidden_states)
        return self.lm_head(hidden_states)

    @torch.no_grad()
    def generate(self, input_ids: torch.Tensor, max_new_tokens: int = 128,
                 temperature: float = 0.7, top_k: int = 50,
                 eos_token_id: (int | None) = None) -> torch.Tensor:
        """Auto-regressive text generation."""
        if eos_token_id is None:
            eos_token_id = self.config.eos_token_id

        generated = input_ids.clone()
        for _ in range(max_new_tokens):
            logits = self.forward(generated[:, -self.config.max_position_embeddings:])
            next_logits = logits[:, -1, :] / temperature

            # Top-k filtering
            if top_k > 0:
                k = min(top_k, next_logits.size(-1))
                top_k_vals, _ = torch.topk(next_logits, k, dim=-1)
                min_top_k = top_k_vals[:, -1].unsqueeze(-1)
                next_logits[next_logits < min_top_k] = float("-inf")

            probs = F.softmax(next_logits, dim=-1)
            next_token = torch.multinomial(probs, num_samples=1)
            generated = torch.cat([generated, next_token], dim=-1)

            if next_token.item() == eos_token_id:
                break

        return generated


def create_qwen35_model(config_path: (str | None) = None) -> Qwen35ForCausalLM:
    """Factory function to create a Qwen35 model from a config file.

    Args:
        config_path: Path to JSON config (default: eve_rebuild/config.json)

    Returns:
        Initialized (untrained) Qwen35ForCausalLM model
    """

    if config_path is None:
        config_path = str(
            Path(__file__).resolve().parents[3] / "workspace" / "projects"
            / "eve-unleashed" / "eve_rebuild" / "config.json"
        )

    config = Qwen35SSMConfig.from_json(config_path)
    return Qwen35ForCausalLM(config)
