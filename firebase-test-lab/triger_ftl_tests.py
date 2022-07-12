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

import gcs
import json
import os
import re
import subprocess
import threading

from absl import app
from absl import flags
from absl import logging
import attr

PROJECT_ID_KEY="project_id"
_IOS = "ios"
_ANDROID = "android"
_XCTEST = "xctest"
_GAMELOOPTEST = "game-loop"
#TODO: create FTL trigger for android tests.

FLAGS = flags.FLAGS

flags.DEFINE_string(
    "testapp_dir", None,
    "Testapps (apks and ipas) in this directory will be tested.")
flags.DEFINE_string(
    "project_id", None, "Path to key file authorizing use of the GCS bucket.")
flags.DEFINE_enum(
    "test_type", 'xctest', [_XCTEST, _GAMELOOPTEST], "Test type that Firebase Test Lab will run.")
flags.DEFINE_string(
    "android_model", None,
    "Model id for desired device. See module docstring for details on how"
    " to get this id. If none, will use FTL's default.")
flags.DEFINE_string(
    "android_version", None,
    "API level for desired device. See module docstring for details on how"
    " to find available values. If none, will use FTL's default.")
flags.DEFINE_string(
    "ios_model", None,
    "Model id for desired device. See module docstring for details on how"
    " to get this id. If none, will use FTL's default.")
flags.DEFINE_string(
    "ios_version", None,
    "iOS version for desired device. See module docstring for details on how"
    " to find available values. If none, will use FTL's default.")

def main(argv):
  if len(argv) > 1:
    raise app.UsageError("Too many command-line arguments.")

  project_id = FLAGS.project_id
  testapp_dir = _fix_path(FLAGS.testapp_dir)
  android_model = FLAGS.android_model
  android_version = FLAGS.android_version
  ios_model = FLAGS.ios_model
  ios_version = FLAGS.ios_version

  ios_device = Device(model=ios_model, version=ios_version)
  android_device = Device(model=android_model, version=android_version)
  testapps=[]
  for file_dir, _, file_names in os.walk(testapp_dir):
    for file_name in file_names:
      full_path = os.path.join(file_dir, file_name)
      if FLAGS.test_type==_XCTEST and file_name.endswith(".zip") :
        print("XCTest bundle, " + full_path + " is detected.")
        has_ios = True
        testapps.append((ios_device, _IOS, full_path))
      elif FLAGS.test_type == _GAMELOOPTEST:
        if file_name.endswith(".apk"):
          testapps.append((android_device, _ANDROID, full_path))
        elif file_name.endswith(".ipa"):
          has_ios = True
          testapps.append((ios_device, _IOS, full_path))


  if not testapps:
    logging.error("No testapps found.")
    return 1

  logging.info("Testapps found: %s", "\n".join(path for _, _, path in testapps))

  gcs_base_dir = gcs.get_unique_gcs_id()
  logging.info("Store results in %s", gcs.relative_path_to_gs_uri(gcs_base_dir, project_id))

  tests = []
  for device, platform, path in testapps:
    # e.g. /testapps/unity/firebase_auth/app.apk -> unity_firebase_auth_app_apk
    rel_path = os.path.relpath(path, testapp_dir)
    name = rel_path.replace("\\", "_").replace("/", "_").replace(".", "_")
    tests.append(
        Test(
            project_id=project_id,
            device=device,
            platform=platform,
            testapp_path=path,
            results_dir=gcs_base_dir + "/" + name))

  logging.info("Sending testapps to FTL")
  tests = _run_test_on_ftl(tests, [])

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
  platform = attr.ib()  # Android or iOS
  testapp_path = attr.ib()
  results_dir = attr.ib()  # Subdirectory on Cloud storage for this testapp
  # This will be populated after the test completes, instead of initialization.
  logs = attr.ib(init=False, default=None)
  ftl_link = attr.ib(init=False, default=None)
  raw_result_link = attr.ib(init=False, default=None)

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
      logging.error("gCloud returned non-zero error code")
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
        "--results-bucket", self.project_id,
        "--results-dir", self.results_dir,
        "--timeout", "600s"
    ]

    if FLAGS.test_type==_XCTEST:
      cmd = [gcs.GCLOUD, "firebase", "test", "ios", "run"]
      test_flags.extend(["--test", self.testapp_path])
    elif FLAGS.test_type == _GAMELOOPTEST:
      if self.platform == _ANDROID:
        cmd = [gcs.GCLOUD, "firebase", "test", "android", "run"]
      elif self.platform == _IOS:
        cmd = [gcs.GCLOUD, "beta", "firebase", "test", "ios", "run"]
      else:
        raise ValueError("Invalid platform, must be 'Android' or 'iOS'")
      test_flags.extend(["--app", self.testapp_path])
    else:
      raise ValueError("Invalid test_type, must be 'XCTEST' or 'GAMELOOPTEST'")
    
    return cmd + self.device.get_gcloud_flags() + test_flags

# All device dimensions are optional: FTL will use default options when
# a dimension isn't specified.
@attr.s(frozen=True, eq=True)
class Device(object):
  """Specifies a device on Firebase Test Lab. All fields are optional."""
  model = attr.ib(default=None)
  version = attr.ib(default=None)

  def get_gcloud_flags(self):
    """Returns flags for gCloud command to use this device on FTL."""
    # e.g. ["--device", "model=shamu,version=23"]
    # FTL supports four device 'dimensions'. model, orientation, lang, and
    # orientation. We leave orientation and lang as the defaults.
    if not self.model and not self.version:
      return []
    gcloud_flags = ["--device"]
    dimensions = []
    if self.model:
      dimensions.append("model=" + self.model)
    if self.version:
      dimensions.append("version=" + self.version)
    gcloud_flags.append(",".join(dimensions))
    return gcloud_flags

if __name__ == "__main__":
  flags.mark_flag_as_required("testapp_dir")
  flags.mark_flag_as_required("key_file")
  app.run(main)

