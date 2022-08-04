# Copyright 2022 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import re
import subprocess
import threading

from absl import app
from absl import flags
from absl import logging
from pathlib import Path
import attr

_IOS = "ios"
_ANDROID = "android"
_XCTEST = "xctest"
_GAMELOOPTEST = "game-loop"
#TODO: create FTL trigger for android tests.

FLAGS = flags.FLAGS

flags.DEFINE_string(
    "project_id", None, "Firebase Project ID.")
flags.DEFINE_string(
    "testapp_dir", None,
    "Testapps (apks and ipas) in this directory will be tested.")
flags.DEFINE_enum(
    "test_type", _GAMELOOPTEST, [_XCTEST, _GAMELOOPTEST], "Test type that Firebase Test Lab will run.")
flags.DEFINE_string(
    "test_devices", None,
    "Model id and device version for desired device."
    "If none, will use FTL's default.")


def main(argv):
  if len(argv) > 1:
    raise app.UsageError("Too many command-line arguments.")

  project_id = FLAGS.project_id
  if not project_id:
    project_id = os.getenv('GCLOUD_PROJECT')
    if not project_id:
      logging.error("GCLOUD Configuration error: missing project id.")
      return 1
  logging.info("project_id: %s", project_id)

  testapp_dir = _fix_path(FLAGS.testapp_dir)
  testapps=[]
  if FLAGS.test_type==_XCTEST:
    testapps.extend(Path(testapp_dir).rglob("*.zip"))
  if FLAGS.test_type == _GAMELOOPTEST:
    testapps.extend(Path(testapp_dir).rglob("*.apk"))
    testapps.extend(Path(testapp_dir).rglob("*.ipa"))
  if not testapps:
    logging.error("No testapps found.")
    return 1
  logging.info("Testapps found: %s", testapps)

  tests = []
  for path in testapps:
    # e.g. /testapps/unity/firebase_auth/app.apk -> unity_firebase_auth_app_apk
    rel_path = os.path.relpath(path, testapp_dir)
    name = rel_path.replace("\\", "_").replace("/", "_").replace(".", "_")
    tests.append(
        Test(
            project_id=project_id,
            device=None,
            testapp_path=str(path)))

  logging.info("Sending testapps to FTL")
  tests = _run_test_on_ftl(tests, [])
  return tests

def _run_test_on_ftl(tests, tested_tests, retry=3):
  threads = []
  for test in tests:
    logging.info("Start running testapp: %s" % test.testapp_path)
    thread = threading.Thread(target=test.run)
    threads.append(thread)
    thread.start()
    tested_tests.append(test)
  for thread in threads:
    thread.join()
  return tested_tests

def _fix_path(path):
  """Expands ~, normalizes slashes, and converts relative paths to absolute."""
  return os.path.abspath(os.path.expanduser(path))

@attr.s(frozen=False, eq=False)
class Test(object):
  """Holds data related to the testing of one testapp."""
  device = attr.ib()
  project_id = attr.ib()
  testapp_path = attr.ib()
  # This will be populated after the test completes, instead of initialization.
  logs = attr.ib(init=False, default=None)
  ftl_link = attr.ib(init=False, default=None)
  raw_result_link = attr.ib(init=False, default=None)
  results_dir = attr.ib(init=False, default=None)  # Subdirectory on Cloud storage for this testapp

  # This runs in a separate thread, so instead of returning values we store
  # them as fields so they can be accessed from the main thread.
  def run(self):
    """Send the testapp to FTL for testing and wait for it to finish."""
    # These execute in parallel, so we collect the output then log it at once.
    args = self._gcloud_command
    
    logging.info("Testapp sent: %s", " ".join(args))
    result = subprocess.run(
        args=args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False)
    logging.info("Finished: %s\n%s", " ".join(args), result.stdout)
    if result.returncode:
      logging.error("gCloud returned non-zero error code: %s", result.returncode)
    ftl_link = re.search(r'Test results will be streamed to \[(.*?)\]', result.stdout, re.DOTALL)
    if ftl_link:
      self.ftl_link = ftl_link.group(1)
    raw_result_link = re.search(r'Raw results will be stored in your GCS bucket at \[(.*?)\]', result.stdout, re.DOTALL)
    if raw_result_link:
      self.raw_result_link = raw_result_link.group(1)

    logging.info("Test done.")

  @property
  def _gcloud_command(self):
    """Returns the args to send this testapp to FTL on the command line."""
    test_flags = [
        "--type", FLAGS.test_type,
        "--timeout", "600s"
    ]

    if FLAGS.test_type==_XCTEST:
      cmd = ["gcloud", "firebase", "test", "ios", "run"]
      test_flags.extend(["--test", self.testapp_path])
    elif FLAGS.test_type == _GAMELOOPTEST:
      if self.testapp_path.endswith(".apk"):
        cmd = ["gcloud", "firebase", "test", "android", "run"]
      else:
        cmd = ["gcloud", "beta", "firebase", "test", "ios", "run"]
      test_flags.extend(["--app", self.testapp_path])
    else:
      raise ValueError("Invalid test_type, must be 'XCTEST' or 'GAMELOOPTEST'")
    
    return cmd + test_flags


if __name__ == "__main__":
  flags.mark_flag_as_required("testapp_dir")
  app.run(main)

