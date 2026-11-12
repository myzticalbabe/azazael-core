#!/usr/bin/env python3
"""Deeper tests for Eve qwen35_ssm model (tiny config)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

torch = pytest.importorskip("torch")


def _tiny_config():
    from eve_unleashed.models.qwen35_ssm import Qwen35SSMConfig
    return Qwen35SSMConfig(
        vocab_size=32, hidden_size=8, intermediate_size=16,
        num_hidden_layers=2, num_attention_heads=2, num_key_value_heads=2,
        max_position_embeddings=16, inner_size=8, state_size=4,
        time_step_rank=2, group_count=2, conv_kernel=2,
        full_attention_interval=2,
    )


def test_config_from_json_missing():
    from eve_unleashed.models.qwen35_ssm import Qwen35SSMConfig
    import tempfile, json
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump({"vocab_size": 10, "hidden_size": 4}, f)
        path = f.name
    cfg = Qwen35SSMConfig.from_json(path)
    assert cfg.vocab_size == 10
    assert cfg.hidden_size == 4
    import os; os.unlink(path)


def test_rmsnorm():
    from eve_unleashed.models.qwen35_ssm import RMSNorm
    import torch
    m = RMSNorm(8, eps=1e-6)
    x = torch.randn(2, 4, 8)
    y = m(x)
    assert y.shape == x.shape


def test_rotary():
    from eve_unleashed.models.qwen35_ssm import RotaryEmbedding
    import torch
    m = RotaryEmbedding(dim=4, max_position_embeddings=8, theta=10000.0)
    x = torch.randn(1, 4, 2, 4)
    pos = torch.arange(4).unsqueeze(0)
    cos, sin = m(x, pos)
    assert cos.shape[-1] == 4
    assert sin.shape[-1] == 4


def test_mlp():
    from eve_unleashed.models.qwen35_ssm import Qwen35MLP
    import torch
    cfg = _tiny_config()
    m = Qwen35MLP(cfg)
    x = torch.randn(1, 4, cfg.hidden_size)
    y = m(x)
    assert y.shape == x.shape


def test_attention():
    from eve_unleashed.models.qwen35_ssm import Qwen35Attention
    import torch
    cfg = _tiny_config()
    m = Qwen35Attention(cfg)
    x = torch.randn(1, 4, cfg.hidden_size)
    pos = torch.arange(4).unsqueeze(0)
    y = m(x, pos)
    assert y.shape == x.shape


def test_ssm():
    from eve_unleashed.models.qwen35_ssm import Qwen35SSM
    import torch
    cfg = _tiny_config()
    m = Qwen35SSM(cfg)
    x = torch.randn(1, 4, cfg.hidden_size)
    y = m(x)
    assert y.shape == x.shape


def test_decoder_layer_both():
    from eve_unleashed.models.qwen35_ssm import Qwen35DecoderLayer
    import torch
    cfg = _tiny_config()
    for idx in (0, 1):
        layer = Qwen35DecoderLayer(cfg, idx)
        x = torch.randn(1, 4, cfg.hidden_size)
        pos = torch.arange(4).unsqueeze(0)
        y = layer(x, pos)
        assert y.shape == x.shape


def test_model_forward_and_generate():
    from eve_unleashed.models.qwen35_ssm import Qwen35ForCausalLM
    import torch
    cfg = _tiny_config()
    m = Qwen35ForCausalLM(cfg)
    ids = torch.tensor([[1, 2, 3]])
    logits = m(ids)
    assert logits.shape == (1, 3, cfg.vocab_size)
    out = m.generate(ids, max_new_tokens=2, eos_token_id=2)
    assert out.shape[0] == 1
    assert out.shape[1] >= 3


def test_apply_rotary():
    from eve_unleashed.models.qwen35_ssm import apply_rotary_pos_emb, rotate_half
    import torch
    q = torch.randn(1, 2, 4, 4)
    k = torch.randn(1, 2, 4, 4)
    cos = torch.ones(1, 4, 4)
    sin = torch.zeros(1, 4, 4)
    qe, ke = apply_rotary_pos_emb(q, k, cos, sin)
    assert qe.shape == q.shape
    assert ke.shape == k.shape
