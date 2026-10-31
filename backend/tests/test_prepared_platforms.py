import json
import pytest
from app.api.simulation import _check_simulation_prepared
from app.config import Config

@pytest.mark.parametrize("reddit,twitter", [(True,False),(False,True),(True,True)])
def test_preparation_requires_only_enabled_platforms(monkeypatch,tmp_path,reddit,twitter):
    monkeypatch.setattr(Config,"OASIS_SIMULATION_DATA_DIR",str(tmp_path))
    folder=tmp_path/"sim-saved"
    folder.mkdir()
    (folder/"state.json").write_text(json.dumps({"status":"running","config_generated":True,
        "enable_reddit":reddit,"enable_twitter":twitter}),encoding="utf-8")
    (folder/"simulation_config.json").write_text("{}",encoding="utf-8")
    if reddit: (folder/"reddit_profiles.json").write_text("[]",encoding="utf-8")
    if twitter: (folder/"twitter_profiles.csv").write_text("user_id,name\n",encoding="utf-8")
    assert _check_simulation_prepared("sim-saved")[0]
    required="reddit_profiles.json" if reddit else "twitter_profiles.csv"
    (folder/required).unlink()
    assert not _check_simulation_prepared("sim-saved")[0]
