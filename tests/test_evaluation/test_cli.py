import json

from evaluation.run import build_parser, main


def test_cli_argument_parsing():
    args = build_parser().parse_args(["--dataset", "d.json", "--limit", "2", "--offline", "--difficulty", "easy"])
    assert args.dataset == "d.json"
    assert args.limit == 2
    assert args.offline is True


def test_offline_cli_validates_without_agent(tmp_path, capsys):
    path = tmp_path / "d.json"
    path.write_text(json.dumps([{"id": "c", "question": "q", "category": "x", "difficulty": "easy"}]), encoding="utf-8")
    assert main(["--dataset", str(path), "--offline"]) == 0
    assert "Agent was not invoked" in capsys.readouterr().out


def test_comparison_cli_writes_json_and_markdown(tmp_path):
    payload = {"run_id": "a", "dataset": "d", "metrics": {"rates": {}, "latency_ms": {}}, "cases": []}
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text(json.dumps(payload), encoding="utf-8")
    payload["run_id"] = "b"
    b.write_text(json.dumps(payload), encoding="utf-8")
    output = tmp_path / "comparison.json"
    assert main(["--compare", str(a), str(b), "--comparison-output", str(output)]) == 0
    assert output.exists()
    assert output.with_suffix(".md").exists()
