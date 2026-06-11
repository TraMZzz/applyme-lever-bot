from applyme.cli import build_parser


def test_parser_defaults_to_dry_run():
    args = build_parser().parse_args(["run", "--vacancies", "v.txt"])
    assert args.submit_mode == "dry-run" and args.max_applies == 5


def test_per_apply_timeout_unset_is_none_and_overridable():
    # Unset → None so run_command falls back to Settings.per_apply_timeout_s.
    assert build_parser().parse_args(["run", "--vacancies", "v.txt"]).per_apply_timeout is None
    args = build_parser().parse_args(["run", "--vacancies", "v.txt", "--per-apply-timeout", "600"])
    assert args.per_apply_timeout == 600.0
