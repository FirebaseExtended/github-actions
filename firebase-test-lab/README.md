# `firebase-test-lab` GitHub Action

This GitHub Action sends your apps to Firebase Test Lab, which lets you test your apps on a range of devices and configurations, and generate test summary as output.

## Prerequisite

-   A Firebase project. Follow the instructions [Setting up a Firebase project and registering apps](https://firebase.google.com/docs/projects/learn-more#setting_up_a_firebase_project_and_registering_apps).

-   Have a service account and enable required APIs. Create a service account with an Editor role in the [Google Cloud Console](https://console.cloud.google.com/iam-admin/serviceaccounts/) and then activate it (see the [gcloud auth activate-service-account documentation](https://cloud.google.com/sdk/gcloud/reference/auth/activate-service-account) to learn how). Using the service account to log into Google and go to the [Google Developers Console API Library page](https://console.developers.google.com/apis/library). Enable the **Google Cloud Testing API** and the **Cloud Tool Results API** by typing their names into the search box at the top of the console and clicking Enable API.

-   Install and authorize the Google Cloud SDK. Firebase Test Lab GitHub Action will do this step for you if you provides **Google Cloud Service Account Key JSON** or **Workload Identity Federation**. Alternatively, you could do it by yourself. For more infomation: [google-github-actions/auth](https://github.com/google-github-actions/auth).

-   Python 2.7+. All the [GitHub-hosted runners](https://docs.github.com/en/actions/using-github-hosted-runners/about-github-hosted-runners) have Python 2.7+ preinstalled. But if you are using a [self-hosted runners](https://docs.github.com/en/actions/hosting-your-own-runners/about-self-hosted-runners), please have Python installed first.

## Usage
```yml
jobs:
  ftl_testing:
    # ...

    steps:
      - uses: actions/checkout@v3
      - name: Build your Apps
        ...
      - id: ftl_test
        uses: FirebaseExtended/github-actions/firebase-test-lab@v1.1
        with:
          testapp_dir: ${{ testapp_dir }}
          test_type: ${{ test_type }}
```

## Inputs

-   `test_type`: One of the following four test types that FTL supports: [xctest](https://firebase.google.com/docs/test-lab/ios/run-xctest), [instrumentation](https://firebase.google.com/docs/test-lab/android/instrumentation-test), [robo](https://firebase.google.com/docs/test-lab/android/robo-ux-test), [game-loop](https://firebase.google.com/docs/test-lab/android/game-loop).

-   `testapp_dir`: Testapps under this dir that will be tested. For XCTest, make sure you [packaged your app](https://firebase.google.com/docs/test-lab/ios/run-xctest#package-app) first. [TODO: needs better design] For instrumentation tests, you also need to package the apks: make sure the test apk name contains string "test" and the app apk doesn't (e.g. app-debug-unaligned.apk & app-debug-test-unaligned.apk -> app.zip).

-   `arg_groups` [**robo** & **instrumentation** test only]: Arguments in a YAML-formatted argument file, separate by ";". If there are conflicts between `arg_groups` and other inputs, arguments in `arg_groups` will be overrided by other inputs.
    Here are the contents of a YAML argument file which is stored in a file named demo_arg.yaml:
    ```yml
    unit-tests:
      type: instrumentation
      app: path/to/excelsior.apk
      test: path/to/excelsior-test.apk  # the unit tests
      timeout: 10m
      device-ids: NexusLowRes
      include: [supported-versions, supported-locales]
    
    unit-tests-2:
    ...
    ```
    `arg_groups` usage example:
    ```yml
    jobs:
    ftl_testing:
        # ...

        steps:
        - uses: actions/checkout@v3
        - name: Build your Apps
            ...
        - id: ftl_test
          uses: FirebaseExtended/github-actions/firebase-test-lab@v1.1
          with:
            arg_groups: demo_arg.yaml:unit-tests;demo_arg.yaml:unit-tests-2
    ```

-   `test_devices`: Devices used for testing, separate by ";". You could find out all the available devices in Test Lab here: [Android](https://firebase.google.com/docs/test-lab/android/available-testing-devices), [iOS](https://firebase.google.com/docs/test-lab/ios/available-testing-devices).
    `test_devices` usage example:
    ```yml
    jobs:
    ftl_testing:
        # ...

        steps:
        - uses: actions/checkout@v3
        - name: Build your Apps
            ...
        - id: ftl_test
          uses: FirebaseExtended/github-actions/firebase-test-lab@v1.1
          with:
            testapp_dir: ${{ testapp_dir }}
            test_type: ${{ test_type }}
            test_devices: model=redfin,version=30;model=oriole,version=33
    ```

-   `timeout` [default: 600s]: The maximum duration you want your test to run. You can enter an integer to represent the duration in seconds, or an integer and enumeration to represent the duration as a longer unit of time. 

-   `max_attempts` [default: 1]: Max retry attempts when test on FTL failed. 

-   (Optional) The following inputs are for installation and authenticating to Google Cloud. Firebase Test Lab GitHub Action leverages [google-github-actions/auth](https://github.com/google-github-actions/auth) and [google-github-actions/setup-gcloud](https://github.com/google-github-actions/setup-gcloud). 
    Example of authenticating via `credentials_json` (Service Account Key JSON). See [Creating and managing Google Cloud Service Account Keys](https://cloud.google.com/iam/docs/creating-managing-service-account-keys) for more information.
    ```yml
    jobs:
    ftl_testing:
        # ...

        steps:
        - uses: actions/checkout@v3
        - name: Build your Apps
            ...
        - id: ftl_test
          uses: FirebaseExtended/github-actions/firebase-test-lab@v1.1
          with:
            credentials_json: ${{ secrets.GOOGLE_CREDENTIALS }}
            testapp_dir: ${{ testapp_dir }}
            test_type: ${{ test_type }}
    ```

    Example of authenticating via `workload_identity_provider` and `service_account` (Workload Identity Federation). See [Setting up Workload Identity Federation](https://github.com/google-github-actions/auth#setup) for more information.
    ```yml
    jobs:
    ftl_testing:
        # ...

        steps:
        - uses: actions/checkout@v3
        - name: Build your Apps
            ...
        - id: ftl_test
          uses: FirebaseExtended/github-actions/firebase-test-lab@v1.1
          with:
            workload_identity_provider: ${{ workload_identity_provider }}
            service_account: ${{ service_account }}
            testapp_dir: ${{ testapp_dir }}
            test_type: ${{ test_type }}
    ```

## Output

This GitHub Action will collection all the test results and generate a summary in the JSON format.

Output usage:
```yml
- id: ftl_test
    uses: FirebaseExtended/github-actions/firebase-test-lab@v1.1
    with:
      ...
- run: echo '${{ steps.ftl_test.outputs.test_summary }}'
```

Test summary example:
```
{
  "project_id": ${project_id},
  "apps": [
    { # app_1
      "attempt": ${attempt_num},
      "cmd": ${cmd},
      "return_code": ${return_code}, # Script exit codes: https://firebase.google.com/docs/test-lab/ios/command-line#script-exit-codes
      "testapp_path": ${app_path},
      "test_type": ${test_type}, # game-loop, xctest, robo, instrumentation
      "ftl_link": ${ftl_link}, # Test Lab page from Firebase console
      "raw_result_link":  ${raw_result_link}, # Google Cloud page that stores all the test artifacts
      "devices": [
        { # device_1
          "device_axis": ${device_axis},
          "outcome": ${outcome}, # Passed, Failed, Inconclusive, Skipped
        },
        { # device_2
          ... 
        }
      ],
    },
    { # app_2
      ...
    }
  ]
}
```
