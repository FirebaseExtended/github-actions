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

r"""Tool for sending mobile testapps to Firebase Test Lab for testing.

Requires Cloud SDK installed with gsutil. Can be checked as follows:
  gcloud --version

This tool will use the GCS storage bucket, thus must be authorized first.
Usage:

  python trigger_ftl_tests.py --testapp_dir ~/testapps --test_type xctest

This will recursively search testapps under dir "~/testapps" for apks, ipas,
and zips, send them to FTL, and generate test summary:

TO-DO: For android instrumentation testapps, needs better design
Note: Currently, please compress both apks in a zip file and make sure the
test apk name contains string "test" and the app apk doesn't. e.g.

  app-debug-unaligned.apk & app-debug-test-unaligned.apk -> app.zip

Anrdoid robo tests & instrumentation tests also accept arguments in a
YAML-formatted argument file. For more information:
https://cloud.google.com/sdk/gcloud/reference/topic/arg-files

Summary example:
  {
    "project_id": ${project_id},
    "apps": [
      { # app_1
        "cmd": ${cmd},
        "return_code": ${return_code}, # Script exit codes.
        "testapp_path": ${app_path},
        "test_type": ${test_type}, # game-loop, xctest, robo, instrumentation
        "ftl_link": ${ftl_link},
        "raw_result_link":  ${raw_result_link},
        "devices": [
          { # device_1
            "device_axis": ${device_axis},
            "outcome": ${outcome}, # Passed, Failed, Inconclusive, Skipped
          }
        ],
      },
    ]
  }

If you wish to specify a particular device to test on, you will need the model
id and version (api level for Android, OS version for iOS). These change over
time. You can find the currently supported models and versions with the
following commands:

  gcloud firebase test android models list
  gcloud firebase test ios models list

Note: you need the value in the MODEL_ID column, not MODEL_NAME.
Examples:
Test on two iOS devices:
  --test_devices "model=iphone8,version=13.6;model=iphone8,version=14.7"

"""

import argparse
import imp
import json
import logging
import os
import platform
import random
import re
import subprocess
import threading
import time

from zipfile import ZipFile


if platform.system() == 'Windows':
  GCLOUD = "gcloud.CMD"
  GSUTIL = "gsutil.CMD"
else:
  GCLOUD = "gcloud"
  GSUTIL = "gsutil"

# Test Types:
XCTEST = "xctest"
ROBO = "robo"
INSTRUMENTATION = "instrumentation"
GAMELOOP = "game-loop"

TEST_ANDROID_CMD = [GCLOUD, "firebase", "test", "android", "run"]
TEST_IOS_CMD = [GCLOUD, "firebase", "test", "ios", "run"]
BETA_TEST_IOS_CMD = [GCLOUD, "beta", "firebase", "test", "ios", "run"]

def main():
  logging.basicConfig(level=logging.DEBUG)

  FLAGS = parse_cmdline_args()
  tests_result = _run_test_on_ftl(FLAGS)
  logging.info("All Tests Done:\n%s" % json.dumps(tests_result, indent=2))
  exit_code = _exit_code(tests_result)
  print("%s %s" % (exit_code, json.dumps(tests_result)))


def _get_project_id(project_id):
  if not project_id:
    # Auto generated by https://github.com/google-github-actions/auth
    project_id = os.getenv('GCLOUD_PROJECT')

  logging.info("project_id: %s", project_id)
  return project_id


def _search_testapps(testapp_dir, test_type):
  testapp_dir = _fix_path(testapp_dir)
  testapps = []
  for file_dir, _, file_names in os.walk(testapp_dir):
    for file_name in file_names:
      full_path = os.path.join(file_dir, file_name)
      if test_type==XCTEST or test_type==INSTRUMENTATION:
        if file_name.endswith(".zip"):
          testapps.append(full_path)
      elif test_type==ROBO:
        if file_name.endswith(".apk"):
          testapps.append(full_path)
      elif test_type == GAMELOOP:
        if file_name.endswith(".apk"):
          testapps.append(full_path)
        elif file_name.endswith(".ipa"):
          testapps.append(full_path)
  logging.info("Testapps found: %s", testapps)
  return testapps


def _fix_path(path):
  """Expands ~, normalizes slashes, and converts relative paths to absolute."""
  return os.path.abspath(os.path.expanduser(path))


def _run_test_on_ftl(FLAGS):
  # Generate ftl cmd for each testapps
  ftl_cmd_list = []

  if FLAGS.testapp_dir and FLAGS.test_type:
    testapps = _search_testapps(FLAGS.testapp_dir, FLAGS.test_type)
    if not testapps:
      logging.error("No testapps found.")
      exit(1)
    for app in testapps:
      cmd = _ftl_cmd_with_flags(FLAGS, app)
      if FLAGS.arg_groups:
        for arg_group in FLAGS.arg_groups.split(";"):
          cmd_extended = cmd[:]
          cmd_extended.append(arg_group)
          ftl_cmd_list.append(cmd_extended)
      else:
        ftl_cmd_list.append(cmd)
  elif FLAGS.arg_groups:
    for arg_group in FLAGS.arg_groups.split(";"):
      ftl_cmd_list.append(_ftl_cmd_with_arg_group(FLAGS, arg_group))

  # Run each ftl cmd in threads
  threads = []
  tests_result = { "project_id": _get_project_id(FLAGS.project_id), "apps": [] }
  logging.info("Sending testapps to FTL")
  for ftl_cmd in ftl_cmd_list:
    thread = threading.Thread(target=_ftl_run, args=(FLAGS, " ".join(ftl_cmd), tests_result))
    threads.append(thread)
    thread.start()
  for thread in threads:
    thread.join()
  return tests_result


# This runs in a separate thread, so instead of returning values we store
# them in test_result so they can be accessed from the main thread.
def _ftl_run(FLAGS, ftl_cmd, tests_result):
  attempt_num = 1
  while attempt_num <= FLAGS.max_attempts:
    logging.info("Testapp sent to FTL: %s (attempt %s of %s)", ftl_cmd, attempt_num, FLAGS.max_attempts)
    result = subprocess.Popen(
        args=ftl_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
        shell=True)
    while result.poll() is None:
      time.sleep(1) # Process hasn't exited yet, let's wait some
    # https://firebase.google.com/docs/test-lab/android/command-line#script_exit_codes
    logging.info("Test done: %s\nReturned code: %s", ftl_cmd, result.returncode)

    test_summary = _parse_test_summary(FLAGS, ftl_cmd, result, attempt_num)
    tests_result.get('apps').append(test_summary)

    if _validate_results(FLAGS, test_summary):
      break

    attempt_num += 1


def _parse_test_summary(FLAGS, ftl_cmd, result, attempt_num):
    """There is no better API avaliable. Thus, use Regex to parse test information, and generate test_summary for this testapp"""
    result_log = result.stdout.read()
    logging.info("Test log: %s", result_log)

    # Use Regex to filter the test information, Until we have better APIs.
    # Generate test summary by using regex search ftl cmd logs
    testapp_path = ""
    testapp_path_search = re.search(r'Uploading \[(.*?)\] to Firebase Test Lab', result_log, re.MULTILINE | re.DOTALL)
    if testapp_path_search:
      testapp_path = testapp_path_search.group(1)

    ftl_link = ""
    ftl_link_search = re.search(r'Test results will be streamed to \[(.*?)\]', result_log, re.MULTILINE | re.DOTALL)
    if ftl_link_search:
      ftl_link = ftl_link_search.group(1)

    raw_result_link = ""
    raw_result_link_search = re.search(r'Raw results will be stored in your GCS bucket at \[(.*?)\]', result_log, re.MULTILINE | re.DOTALL)
    if raw_result_link_search:
      raw_result_link = raw_result_link_search.group(1)

    outcome_device = []
    # Test outcome pattern 1:
    # ┌─────────┬──────────────────────────────┬─────────────────────┐
    # │ OUTCOME │       TEST_AXIS_VALUE        │     TEST_DETAILS    │
    # ├─────────┼──────────────────────────────┼─────────────────────┤
    # │ Failed  │ iphone13pro-15.2-en-portrait │ 1 test cases failed │
    # └─────────┴──────────────────────────────┴─────────────────────┘
    outcome_device_search = re.findall(r'│(.*?)│(.*?)│(.*?)│', result_log, re.MULTILINE | re.DOTALL)
    for o_d in outcome_device_search:
      if 'OUTCOME' in o_d[0]: # skip the table title
        continue
      outcome_device.append({"device_axis": o_d[1].strip(), "outcome": o_d[0].strip()})
    # Test outcome pattern 2:
    # OUTCOME: Passed
    # TEST_AXIS_VALUE: redfin-30-en-portrait
    # TEST_DETAILS: --
    outcome_device_search = re.findall(r'OUTCOME:(.*?)\nTEST_AXIS_VALUE:(.*?)\nTEST_DETAILS:', result_log, re.MULTILINE | re.DOTALL)
    for o_d in outcome_device_search:
      outcome_device.append({"device_axis": o_d[1].strip(), "outcome": o_d[0].strip()})

    return {
      "attempt": attempt_num,
      "cmd": ftl_cmd,
      "return_code": result.returncode,
      "testapp_path": testapp_path,
      "test_type": FLAGS.test_type,
      "ftl_link": ftl_link,
      "raw_result_link":  raw_result_link,
      "devices": outcome_device
    }


def _validate_results(FLAGS, test_summary):
  """Returns True if all tests passed; False otherwise"""
  if test_summary.get("return_code") != 0:
    return False

  if FLAGS.test_type == GAMELOOP and FLAGS.validator:
    try:
      # [Experimental] This is for game-loop test only, which could validate test result for one app.
      # Assume FLAGS.validator it the path of customized python script, and contains function "validate(test_summary)"".
      module = os.path.splitext(FLAGS.validator)[0]
      validator = imp.load_source(module, FLAGS.validator)
      return validator.validate(test_summary)
    except ImportError:
      logging.error("ImportError with validator: %s", FLAGS.validator)

  return True


def _ftl_cmd_with_arg_group(FLAGS, arg_group):
  """Returns the cmd with a YAML-formatted argument file. Only support robo & instrumentation tests"""
  cmd = TEST_ANDROID_CMD[:]
  test_flags = [arg_group, "--type", FLAGS.test_type, "--timeout", FLAGS.timeout]
  if FLAGS.test_devices:
    for device in FLAGS.test_devices.split(";"):
      test_flags.extend(["--device", device])
  if FLAGS.additional_flags:
    test_flags.extend(FLAGS.additional_flags.split())

  cmd.append(arg_group)
  return cmd


def _ftl_cmd_with_flags(FLAGS, testapp_path):
  """Returns the cmd to send this testapp to FTL on the command line."""
  if FLAGS.test_type==XCTEST:
    cmd = TEST_IOS_CMD[:]
  elif FLAGS.test_type==ROBO or FLAGS.test_type==INSTRUMENTATION:
    cmd = TEST_ANDROID_CMD[:]
  elif FLAGS.test_type == GAMELOOP:
    if testapp_path.endswith(".ipa"):
      cmd = BETA_TEST_IOS_CMD[:]
    else:
      cmd = TEST_ANDROID_CMD[:]
  else:
    raise ValueError("Invalid test_type")

  if FLAGS.test_type==XCTEST:
    test_flags = ["--test", testapp_path]
  elif FLAGS.test_type==ROBO or FLAGS.test_type == GAMELOOP:
    test_flags = ["--app", testapp_path]
  elif FLAGS.test_type==INSTRUMENTATION:
    (app_path, test_path) = _extract_instrumentation_test(testapp_path)
    test_flags = ["--app", app_path, "--test", test_path]

  test_flags.extend(["--type", FLAGS.test_type, "--timeout", FLAGS.timeout])
  if FLAGS.test_devices:
    test_device_list = FLAGS.test_devices.split(";")
    if FLAGS.test_device_selection == "random":
      test_flags.extend(["--device", random.choice(test_device_list)])
    else:  # FLAGS.test_device_list == "all"
      for device in test_device_list:
        test_flags.extend(["--device", device])
  if FLAGS.additional_flags:
    test_flags.extend(FLAGS.additional_flags.split())

  cmd.extend(test_flags)
  return cmd


def _extract_instrumentation_test(zip_path):
  # Android instrumentation tests requires two apks.
  # https://firebase.google.com/docs/test-lab/android/command-line#running_your_instrumentation_tests
  # Please make sure the test apk name contains string "test" and the app apk doesn't.
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
  """0: all tests passed; 1: some tests failed."""
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
    default=None, help='Firebase Project ID.')
  parser.add_argument('-a', '--arg_groups',
    default=None, help='Arguments in a YAML-formatted argument file.')
  parser.add_argument('-d', '--testapp_dir',
    default=None, help='Testapps (apks, ipas, zips) in this directory will be tested.')
  parser.add_argument('-t', '--test_type',
    default=None, help='Test type that Firebase Test Lab will run..')
  parser.add_argument('--test_devices',
    default=None, help='Model id and device version for desired device. If none, will use FTL default.')
  parser.add_argument('--test_device_selection',
    default='all', choices=['all', 'random'],
    help='Whether to run on all test_devices or on one random device.')
  parser.add_argument('--timeout',
    default="600s", help='Timeout for one ftl test.')
  parser.add_argument('--additional_flags',
    default=None, help='Additional flags that may be used.')
  parser.add_argument('--max_attempts',
    default=1, type=int, help='Max attempts when test on FTL failed.')
  parser.add_argument('--validator',
    default=None, help='Customized python script that validate one test app result.')
  args = parser.parse_args()
  if not (args.arg_groups or (args.testapp_dir and args.test_type)):
    raise ValueError("Must specify --arg_groups or (--testapp_dir and --test_type)")
  return args


if __name__ == '__main__':
  main()
