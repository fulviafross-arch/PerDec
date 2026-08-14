import json, tempfile, unittest
from pathlib import Path
from runner.core import RunConfig, command_for, create_run_dir, validate_artifacts
from runner.__main__ import config_from_args

class RunnerTests(unittest.TestCase):
    def config(self, root): return RunConfig(Path("C:/含 空格/Game.exe"), "项目", "实验", "001", "场景", "001", "abc", "main", artifacts_root=Path(root))
    def valid(self, d, rid):
        (d/"run.json").write_text(json.dumps({"schema_version":"0.1.0-draft","run_id":rid,"status":"completed","failure_reason":None}), encoding="utf-8")
        (d/"events.jsonl").write_text('{"event_type":"run_started"}\n{"event_type":"run_completed"}\n', encoding="utf-8")
        (d/"frames.csv").write_text("run_id,timestamp_ms,frame_time_ms,memory_used_bytes,cpu_frame_time_ms\n"+f"{rid},0,16,100,null\n{rid},5000,17,101,null\n", encoding="utf-8")
        (d/"player.log").write_text("normal", encoding="utf-8")
    def test_unicode_argument_array(self):
        with tempfile.TemporaryDirectory() as root:
            rid,d=create_run_dir(self.config(root)); cmd=command_for(self.config(root),rid,d,1)
            self.assertEqual(Path(cmd[0]),Path("C:/含 空格/Game.exe")); self.assertEqual(cmd[cmd.index("-logFile")+1],str(d/"player.log"))
    def test_uuid_directories_do_not_overwrite(self):
        with tempfile.TemporaryDirectory() as root: self.assertNotEqual(create_run_dir(self.config(root))[1],create_run_dir(self.config(root))[1])
    def test_completed_is_eligible(self):
        with tempfile.TemporaryDirectory() as root:
            rid,d=create_run_dir(self.config(root)); self.valid(d,rid); self.assertTrue(validate_artifacts(d,rid,5).eligible)
    def test_cancelled_bad_frame_and_log_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            rid,d=create_run_dir(self.config(root)); self.valid(d,rid)
            (d/"run.json").write_text(json.dumps({"schema_version":"0.1.0-draft","run_id":rid,"status":"cancelled","failure_reason":None}),encoding="utf-8")
            self.assertFalse(validate_artifacts(d,rid,5).eligible)
            self.valid(d,rid); (d/"frames.csv").write_text(f"run_id,timestamp_ms,frame_time_ms\n{rid},3,NaN\n{rid},2,Infinity\n",encoding="utf-8")
            result=validate_artifacts(d,rid,0); self.assertFalse(result.eligible); self.assertIn("invalid_metric_value:frame_time_ms",result.reasons)
            self.valid(d,rid); (d/"player.log").write_text("Unhandled Exception",encoding="utf-8")
            self.assertFalse(validate_artifacts(d,rid,5).eligible)
    def test_missing_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            rid,d=create_run_dir(self.config(root)); self.assertFalse(validate_artifacts(d,rid,5).eligible)
    def test_config_file_and_command_line_override(self):
        with tempfile.TemporaryDirectory() as root:
            config_path=Path(root)/"config.json"
            config_path.write_text(json.dumps({
                "exe":"C:/含 空格/Game.exe","project_id":"项目","experiment_id":"实验",
                "experiment_version":"001","scenario_id":"场景","scenario_version":"001",
                "commit_sha":"abc","branch":"main","measurement_seconds":5
            }),encoding="utf-8")
            config=config_from_args(["--config",str(config_path),"--measurement-seconds","30"])
            self.assertEqual(config.measurement_seconds,30)
            self.assertEqual(config.project_id,"项目")
    def test_nested_collector_failure_reason_is_supported(self):
        with tempfile.TemporaryDirectory() as root:
            rid,d=create_run_dir(self.config(root)); self.valid(d,rid)
            (d/"run.json").write_text(json.dumps({
                "schema_version":"0.1.0-draft","run_id":rid,"status":"completed",
                "collector":{"failure_reason":None}
            }),encoding="utf-8")
            self.assertTrue(validate_artifacts(d,rid,5).eligible)
            (d/"run.json").write_text(json.dumps({
                "schema_version":"0.1.0-draft","run_id":rid,"status":"completed",
                "failure_reason":None,"collector":{"failure_reason":"collector failed"}
            }),encoding="utf-8")
            result=validate_artifacts(d,rid,5)
            self.assertIn("manifest_failure_reason_conflict",result.reasons)
if __name__ == "__main__": unittest.main()
