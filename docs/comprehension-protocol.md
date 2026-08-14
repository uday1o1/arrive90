# Rider comprehension protocol

Milestone 7 requires exactly eight participants who did not build Arrive90 and did not see an earlier evaluated interface.
The interface and question wording freeze before the first session.
Each participant completes the direct `TARGET_NOT_MET`, transfer, degraded, and recovery paths without coaching about the answers.

After the workflow, ask these two required questions in order.

1. Does the displayed deadline probability guarantee that the rider will arrive by the deadline, or is it an estimate with uncertainty?
2. When the interface says `TARGET_NOT_MET`, does that mean the route is impossible, or that no supported route reached the requested probability target within the extra-time cap?

A participant passes only by identifying the probability as an estimate rather than a guarantee and by explaining that `TARGET_NOT_MET` is a threshold decision rather than impossibility.
At least seven of the eight participants must pass both checks.
Record no name, contact detail, rider identity, coordinate, or trip history.
Use a random study identifier and the four booleans in the scoring schema.

The input JSON has this shape.

```json
{
  "responses": [
    {
      "participant_id": "random-study-id",
      "independent": true,
      "saw_earlier_evaluated_interface": false,
      "estimate_not_guarantee_correct": true,
      "target_not_met_correct": true
    }
  ]
}
```

Score a frozen cohort with the following command.

```sh
uv run python scripts/score_comprehension.py \
  --input path/to/cohort.json \
  --output artifacts/reports/usability/comprehension-v1-cohort-1.json
```

If one comprehension-driven interface revision is needed, freeze the revision and recruit eight entirely fresh participants.
Pass the first report through `--prior-cohort-report` so identifier reuse fails closed.
Never edit or replace an earlier cohort report.

No participant sessions have been run in the current repository evidence state.
That missing external study is a Milestone 7 acceptance blocker rather than a passing or inconclusive result.
