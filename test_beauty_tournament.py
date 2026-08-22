import csv
from pathlib import Path

from beauty_tournament import BeautyTournament, fit_bradley_terry


def test_bradley_terry_orders_repeated_winner():
    votes = [{"left_id": "a", "right_id": "b", "choice": "left"} for _ in range(5)]
    scores = fit_bradley_terry(["a", "b", "c"], votes)
    assert scores["a"] > scores["c"] > scores["b"]


def test_tournament_persists_vote_and_undo(tmp_path: Path):
    images = []
    for number in (1, 2):
        path = tmp_path / f"{number}.jpg"
        path.write_bytes(b"image")
        images.append(path)
    pool = tmp_path / "pool.csv"
    with pool.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["sample", "title", "source", "local_image"])
        writer.writeheader()
        for number, path in enumerate(images, 1):
            writer.writerow({"sample": number, "title": str(number), "source": "test", "local_image": path})
    state = tmp_path / "state.json"
    tournament = BeautyTournament(pool, state)
    pair = tournament.snapshot()["pair"]
    result = tournament.vote(pair[0]["id"], pair[1]["id"], "left")
    assert result["decisions"] == 1
    assert BeautyTournament(pool, state).snapshot()["decisions"] == 1
    assert tournament.undo()["decisions"] == 0
