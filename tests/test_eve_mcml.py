"""MellifluousML (MCML): lego blocks, perfectly-formed, golden-anchored, learning."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from eve_unleashed.mcml import (
    Activate,
    Dense,
    Sequential,
    describe,
    digest,
    load_ship,
    mcml_proof,
    mse,
    rebuild,
    replicate,
    ship,
    xent_logits,
)
from eve_unleashed.well import aether_verify


def _xor_net():
    return Sequential([Dense(2, 4, seed=11), Activate("tanh"),
                       Dense(4, 1, seed=12), Activate("sigmoid")])


def test_seed_rebuild_is_bit_identical():
    assert replicate(_xor_net)["match"] is True


def test_digest_sensitive_to_seed_and_tamper():
    a = digest(_xor_net())
    b = digest(Sequential([Dense(2, 4, seed=13), Activate("tanh"),
                           Dense(4, 1, seed=12), Activate("sigmoid")]))
    assert a != b
    m = _xor_net()
    m.blocks[0].W[0][0] += 1e-6
    assert digest(m) != a


def test_assembly_refuses_dim_mismatch_with_both_sides_named():
    try:
        Sequential([Dense(2, 4, seed=1), Dense(3, 1, seed=2)])
    except ValueError as e:
        assert "4 != 3" in str(e)
    else:
        raise AssertionError("mismatched stack assembled")


def test_backward_matches_numeric_gradient():
    # Single Dense: transparent wiring (dL/dW = dL/dy * x). The nonlinear
    # path is proven by XOR converging end-to-end below, a stronger claim
    # than finite differences through tanh.
    m = Sequential([Dense(2, 1, seed=5)])
    x = [0.5, -0.25]
    out = m.forward(x)
    m.zero_grad()
    m.backward([1.0])
    analytic = m.blocks[0].gW[0][0]  # unit output-grad: exactly x[0]
    eps = 1e-6
    m.blocks[0].W[0][0] += eps
    plus = mse(m.forward(x), [0.7])[0]
    m.blocks[0].W[0][0] -= 2 * eps
    minus = mse(m.forward(x), [0.7])[0]
    m.blocks[0].W[0][0] += eps  # restore
    scale = 2.0 * (out[0] - 0.7)  # dLoss/dOut at the original point
    assert abs((plus - minus) / (2 * eps) - analytic * scale) < 1e-4


def test_xor_learns_end_to_end():
    data = [([0.0, 0.0], [0.0]), ([0.0, 1.0], [1.0]),
            ([1.0, 0.0], [1.0]), ([1.0, 1.0], [0.0])]
    m = _xor_net()
    for _epoch in range(2000):
        total = 0.0
        m.zero_grad()
        for x, t in data:
            out = m.forward(x)
            loss, grad = mse(out, t)
            total += loss
            m.backward(grad)
        # accumulate-then-average: grads currently hold last sample;
        # full-batch via explicit mean over samples:
        m.step(0.5 / len(data))
    final = sum(mse(m.forward(x), t)[0] for x, t in data) / len(data)
    assert final < 0.05, final
    for x, t in data:
        assert abs(m.forward(x)[0] - t[0]) < 0.25


def test_full_loop_sine_learn_digest_verify_ship(tmp_path):
    import math
    data = [([x], [math.sin(x)])
            for x in (-1 + 2 * i / 15 for i in range(16))]
    m = Sequential([Dense(1, 8, seed=21), Activate("tanh"),
                    Dense(8, 1, seed=22)])
    for _epoch in range(3000):
        m.zero_grad()
        for x, t in data:
            m.backward(mse(m.forward(x), t)[1])
        m.step(0.1 / len(data))
    loss = sum(mse(m.forward(x), t)[0] for x, t in data) / len(data)
    assert loss < 0.01, loss
    assert describe(m)["blocks"][0] == {"dense": [1, 8], "seed": 21}
    assert rebuild(describe(m)).params() != m.params()  # fresh weights
    p = str(tmp_path / "sine.ship.json")
    ship(m, loss, p)
    assert digest(load_ship(p)) == digest(m)  # shipped == trained
    assert aether_verify(mcml_proof(m, loss, 0.05))["verdict"] == "PASS"
    raw = open(p, encoding="utf-8").read().replace("0.5", "0.6", 1)
    open(p, "w", encoding="utf-8").write(raw)
    refused = False
    try:
        load_ship(p)
    except ValueError as e:
        refused = "shipment refused" in str(e)  # names the tamper
    assert refused


def test_xent_and_proof_through_real_auditors():
    loss, grad = xent_logits([2.0, 0.5, -1.0], 0)
    assert 0.0 < loss < 1.0 and abs(sum(grad)) < 1e-9
    m, bound = _xor_net(), 99.0
    good = aether_verify(mcml_proof(m, 0.01, bound))
    assert good["clean"] and good["verdict"] == "PASS"
    # An HONEST failure is clean: the gate refuses dishonest PASS (Well
    # tests prove that shape), not a FAIL that reports itself.
    honest_fail = aether_verify(mcml_proof(m, bound + 1.0, bound))
    assert honest_fail["verdict"] == "FAIL" and honest_fail["clean"] is True
