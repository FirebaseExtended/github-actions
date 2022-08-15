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
import platform
import time

from absl import app
from absl import flags
from absl import logging
from zipfile import ZipFile

_XCTEST = "xctest"
_ROBO = "robo"
_INSTRUMENTATION = "instrumentation"
_GAMELOOPTEST = "game-loop"
#TODO: create FTL trigger for android tests.

if platform.system() == 'Windows':
  GCLOUD = "gcloud.CMD"
  GSUTIL = "gsutil.CMD"
else:
  GCLOUD = "gcloud"
  GSUTIL = "gsutil"

FLAGS = flags.FLAGS

flags.DEFINE_string(
    "project_id", None, "Firebase Project ID.")
flags.DEFINE_string(
    "testapp_dir", None,
    "Testapps (apks and ipas) in this directory will be tested.")
flags.DEFINE_enum(
    "test_type", None, [_XCTEST, _ROBO, _INSTRUMENTATION, _GAMELOOPTEST], 
    "Test type that Firebase Test Lab will run.")
flags.DEFINE_string(
    "test_devices", None,
    "Model id and device version for desired device."
    "If none, will use FTL's default.")
flags.DEFINE_string(
    "timeout", "600s",
    "Timeout for one ftl test.")
flags.DEFINE_integer(
    "retry", 0,
    "Retry time on failed testapps.")


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
  for file_dir, _, file_names in os.walk(testapp_dir):
    for file_name in file_names:
      full_path = os.path.join(file_dir, file_name)
      if FLAGS.test_type==_XCTEST:
        if file_name.endswith(".zip"):
          testapps.append(full_path)
      elif FLAGS.test_type==_ROBO:
        if file_name.endswith(".apk"):
          testapps.append(full_path)
      elif FLAGS.test_type==_INSTRUMENTATION:
        if file_name.endswith(".zip"):
          testapps.append(full_path)
      elif FLAGS.test_type == _GAMELOOPTEST:
        if file_name.endswith(".apk"):
          testapps.append(full_path)
        elif file_name.endswith(".ipa"):
          testapps.append(full_path)

  if not testapps:
    logging.error("No testapps found.")
    return 1
  logging.info("Testapps found: %s", testapps)

  logging.info("Sending testapps to FTL")
  return _run_test_on_ftl(project_id, testapps)


def _run_test_on_ftl(project_id, testapps):
  threads = []
  tests_result = { "project_id": project_id, "apps": [] }
  for app in testapps:
    logging.info("Start running testapp: %s" % app)
    thread = threading.Thread(target=_ftl_run, args=(app, tests_result))
    threads.append(thread)
    thread.start()
  for thread in threads:
    thread.join()
  return tests_result

def _fix_path(path):
  """Expands ~, normalizes slashes, and converts relative paths to absolute."""
  return os.path.abspath(os.path.expanduser(path))

# This runs in a separate thread, so instead of returning values we store
# them as fields so they can be accessed from the main thread.
def _ftl_run(testapp_path, tests_result):
  """Send the testapp to FTL for testing and wait for it to finish."""
  # These execute in parallel, so we collect the output then log it at once.
  args = _gcloud_command(testapp_path)
  
  logging.info("Testapp sent: %s", " ".join(args))
  result = subprocess.Popen(
      args=" ".join(args),
      stdout=subprocess.PIPE,
      stderr=subprocess.STDOUT,
      universal_newlines=True, 
      shell=True)
  result_log = result.stdout.read()
  logging.info("Finished: %s\n%s", " ".join(args), result_log)

  ftl_link_search = re.search(r'Test results will be streamed to \[(.*?)\]', result_log, re.MULTILINE | re.DOTALL)
  if ftl_link_search:
    ftl_link = ftl_link_search.group(1)
 
  raw_result_link_search = re.search(r'Raw results will be stored in your GCS bucket at \[(.*?)\]', result_log, re.MULTILINE | re.DOTALL)
  if raw_result_link_search:
    raw_result_link = raw_result_link_search.group(1)
  
  outcome_device = []
  # Pattern 1
  # ┌─────────┬──────────────────────────────┬─────────────────────┐
  # │ OUTCOME │       TEST_AXIS_VALUE        │     TEST_DETAILS    │
  # ├─────────┼──────────────────────────────┼─────────────────────┤
  # │ Failed  │ iphone13pro-15.2-en-portrait │ 1 test cases failed │
  # └─────────┴──────────────────────────────┴─────────────────────┘
  outcome_device_search = re.findall(r'│(.*?)│(.*?)│(.*?)│', result_log, re.MULTILINE | re.DOTALL)
  for o_d in outcome_device_search:
    if 'OUTCOME' in o_d[0]:
      continue
    outcome_device.append({"device_axis": o_d[1].strip(), "outcome": o_d[0].strip()})
  # Pattern 2
  # OUTCOME: Passed
  # TEST_AXIS_VALUE: redfin-30-en-portrait
  # TEST_DETAILS: --
  outcome_device_search = re.findall(r'OUTCOME:(.*?)\nTEST_AXIS_VALUE:(.*?)\nTEST_DETAILS:', result_log, re.MULTILINE | re.DOTALL)
  for o_d in outcome_device_search:
    outcome_device.append({"device_axis": o_d[1].strip(), "outcome": o_d[0].strip()})

  while result.poll() is None:
    # Process hasn't exited yet, let's wait some
    time.sleep(1)
  logging.info("Test done. Returned code: %s", result.returncode)
  
  test_summary =  {
    "return_code": result.returncode,
    "testapp_path": testapp_path,
    "test_type": FLAGS.test_type,
    "ftl_link": ftl_link,
    "raw_result_link":  raw_result_link,
    "devices": outcome_device
  }
  tests_result.get('apps').append(test_summary)


def _gcloud_command(testapp_path):
  """Returns the args to send this testapp to FTL on the command line."""
  test_flags = [
      "--type", FLAGS.test_type,
      "--timeout", FLAGS.timeout
  ]
  android_devices = [
      "--device", "model=gts4lltevzw,version=28",
      "--device", "model=redfin,version=30",
      "--device", "model=oriole,version=33"
  ]
  ios_devices = [
      "--device", "model=iphonexr,version=12.4",
      "--device", "model=iphone8,version=13.6",
      "--device", "model=iphone13pro,version=15.2"
  ]
  if FLAGS.test_type==_XCTEST:
    cmd = [GCLOUD, "firebase", "test", "ios", "run", "--test", testapp_path]
    cmd.extend(ios_devices)
  elif FLAGS.test_type==_ROBO:
    cmd = [GCLOUD, "firebase", "test", "android", "run", "--app", testapp_path]
    cmd.extend(android_devices)
  elif FLAGS.test_type==_INSTRUMENTATION:
    (app_path, test_path) = _extract_android_test(testapp_path)
    cmd = [GCLOUD, "firebase", "test", "android", "run", "--app", app_path, "--test", test_path]
    cmd.extend(android_devices)
  elif FLAGS.test_type == _GAMELOOPTEST:
    if testapp_path.endswith(".ipa"):
      cmd = [GCLOUD, "beta", "firebase", "test", "ios", "run", "--app", testapp_path]
      cmd.extend(ios_devices)
    else:
      cmd = [GCLOUD, "firebase", "test", "android", "run", "--app", testapp_path]
      cmd.extend(android_devices)
  else:
    raise ValueError("Invalid test_type")
    
  cmd.extend(test_flags)

  return cmd


def _extract_android_test(zip_path): 
  with ZipFile(zip_path, 'r') as zipObj:
    output_dir = os.path.splitext(zip_path)[0]
    zipObj.extractall(output_dir)
    for file_dir, _, file_names in os.walk(output_dir):
      for file_name in file_names:
        if file_name.endswith(".apk"):
          full_path = os.path.join(file_dir, file_name)
          if "test" in file_name.lower():
            test_path = full_path
          else:
            app_path = full_path
  return (app_path, test_path)


if __name__ == "__main__":
  flags.mark_flag_as_required("testapp_dir")
  app.run(main)

