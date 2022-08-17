# coding: utf-8
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
import argparse
import logging
import json

from zipfile import ZipFile

_XCTEST = "xctest"
_ROBO = "robo"
_INSTRUMENTATION = "instrumentation"
_GAMELOOPTEST = "game-loop"

if platform.system() == 'Windows':
  GCLOUD = "gcloud.CMD"
  GSUTIL = "gsutil.CMD"
else:
  GCLOUD = "gcloud"
  GSUTIL = "gsutil"

TEST_ANDROID_CMD = [GCLOUD, "firebase", "test", "android", "run"]
TEST_IOS_CMD = [GCLOUD, "firebase", "test", "ios", "run"]
BETA_TEST_IOS_CMD = [GCLOUD, "beta", "firebase", "test", "ios", "run"]

def main():
  FLAGS = parse_cmdline_args()

  logging.basicConfig(level=logging.DEBUG)
  logging.getLogger(__name__)

  project_id = _get_project_id(FLAGS.project_id)
  if not project_id:
    logging.error("GCLOUD Configuration error: missing project id.")
    exit(1)

  testapps = _search_testapps(FLAGS.testapp_dir, FLAGS.test_type)
  if not testapps:
    logging.error("No testapps found.")
    exit(1)

  logging.info("Sending testapps to FTL")
  tests_result = _run_test_on_ftl(FLAGS, project_id, testapps)
  logging.info("All Tests Done:\n%s" % json.dumps(tests_result, indent=2))
  exit_code = _exit_code(tests_result)
  print("%s %s" % (exit_code, json.dumps(tests_result)))


def _get_project_id(project_id):
  if not project_id:
    project_id = os.getenv('GCLOUD_PROJECT')

  logging.info("project_id: %s", project_id)
  return project_id


def _search_testapps(testapp_dir, test_type):
  testapp_dir = _fix_path(testapp_dir)
  testapps = []
  for file_dir, _, file_names in os.walk(testapp_dir):
    for file_name in file_names:
      full_path = os.path.join(file_dir, file_name)
      if test_type==_XCTEST:
        if file_name.endswith(".zip"):
          testapps.append(full_path)
      elif test_type==_ROBO:
        if file_name.endswith(".apk"):
          testapps.append(full_path)
      elif test_type==_INSTRUMENTATION:
        if file_name.endswith(".zip"):
          testapps.append(full_path)
      elif test_type == _GAMELOOPTEST:
        if file_name.endswith(".apk"):
          testapps.append(full_path)
        elif file_name.endswith(".ipa"):
          testapps.append(full_path)
  logging.info("Testapps found: %s", testapps)
  return testapps


def _fix_path(path):
  """Expands ~, normalizes slashes, and converts relative paths to absolute."""
  return os.path.abspath(os.path.expanduser(path))


def _run_test_on_ftl(FLAGS, project_id, testapps):
  threads = []
  tests_result = { "project_id": project_id, "apps": [] }
  for app in testapps:
    thread = threading.Thread(target=_ftl_run, args=(FLAGS, app, tests_result))
    threads.append(thread)
    thread.start()
  for thread in threads:
    thread.join()
  return tests_result


# This runs in a separate thread, so instead of returning values we store
# them as fields so they can be accessed from the main thread.
def _ftl_run(FLAGS, testapp_path, tests_result):
  """Send the testapp to FTL for testing and wait for it to finish."""
  # These execute in parallel, so we collect the output then log it at once.
  args = _gcloud_command(FLAGS, testapp_path)
  
  logging.info("Testapp sent: %s", " ".join(args))
  result = subprocess.Popen(
      args=" ".join(args),
      stdout=subprocess.PIPE,
      stderr=subprocess.STDOUT,
      universal_newlines=True, 
      shell=True)
  result_log = result.stdout.read()

  ftl_link = ""
  ftl_link_search = re.search(r'Test results will be streamed to \[(.*?)\]', result_log, re.MULTILINE | re.DOTALL)
  if ftl_link_search:
    ftl_link = ftl_link_search.group(1)
 
  raw_result_link = ""
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
  logging.info("Test done: %s\nReturned code: %s\n%s", " ".join(args), result.returncode, result_log)
  
  test_summary =  {
    "return_code": result.returncode,
    "testapp_path": testapp_path,
    "test_type": FLAGS.test_type,
    "ftl_link": ftl_link,
    "raw_result_link":  raw_result_link,
    "devices": outcome_device
  }
  tests_result.get('apps').append(test_summary)


def _gcloud_command(FLAGS, testapp_path):
  """Returns the args to send this testapp to FTL on the command line."""
  if FLAGS.test_type==_XCTEST:
    cmd = TEST_IOS_CMD
  elif FLAGS.test_type==_ROBO or FLAGS.test_type==_INSTRUMENTATION:
    cmd = TEST_ANDROID_CMD
  elif FLAGS.test_type == _GAMELOOPTEST:
    if testapp_path.endswith(".ipa"):
      cmd = BETA_TEST_IOS_CMD
    else:
      cmd = TEST_ANDROID_CMD
  else:
    raise ValueError("Invalid test_type")

  if FLAGS.test_type==_XCTEST:
    test_flags = ["--test", testapp_path]
  elif FLAGS.test_type==_ROBO or FLAGS.test_type == _GAMELOOPTEST:
    test_flags = ["--app", testapp_path]
  elif FLAGS.test_type==_INSTRUMENTATION:
    (app_path, test_path) = _extract_android_test(testapp_path)
    test_flags = ["--app", app_path, "--test", test_path]

  test_flags.extend(["--type", FLAGS.test_type, "--timeout", FLAGS.timeout])
  if FLAGS.test_devices:
    for device in FLAGS.test_devices.split(";"):
      test_flags.extend(["--device", device])
  if FLAGS.additional_flags:
    test_flags.extend(FLAGS.additional_flags.split())

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


def _exit_code(tests_result):
  if not tests_result.get("apps"):
    return 1

  for testapp in tests_result.get("apps"):
    if testapp.get("return_code") != 0:
      logging.info("At least one test Failed.")
      return 1

  return 0


def parse_cmdline_args():
  parser = argparse.ArgumentParser(description='FTL Test trigger.')
  parser.add_argument('-p', '--project_id',
    default=None, help='Firebase Project ID..')
  parser.add_argument('-a', '--arg_files', 
    default=None, help='Arguments in a YAML-formatted argument file.')
  parser.add_argument('-d', '--testapp_dir',
    default=None, help='Testapps (apks, ipas, zips) in this directory will be tested.')
  parser.add_argument('-t', '--test_type',
    default=None, help='Test type that Firebase Test Lab will run..')
  parser.add_argument('-m', '--test_devices',
    default=None, help='Model id and device version for desired device. If none, will use FTL default.')
  parser.add_argument('-o', '--timeout', 
    default="600s", help='Timeout for one ftl test.')
  parser.add_argument('-f', '--additional_flags', 
    default=None, help='Additional flags that may be used.')
  args = parser.parse_args()
  if not args.arg_files and (not args.testapp_dir and not args.test_type):
    raise ValueError("Must specify --arg_files or (--testapp_dir and --test_type)")
  return args


if __name__ == '__main__':
  main()
