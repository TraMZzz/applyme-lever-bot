from applyme.cli import build_parser


def test_parser_defaults_to_dry_run():
    args = build_parser().parse_args(["run", "--vacancies", "v.txt"])
    assert args.submit_mode == "dry-run" and args.max_applies == 5
