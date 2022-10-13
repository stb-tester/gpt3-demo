#!/usr/bin/env python3

# Set $OPENAI_API_KEY to your API key

import argparse
import inspect
import logging
import os
import re
import sys
import time
from contextlib import contextmanager
from pprint import pprint

import openai
import stbt


stbt.TEST_PACK_ROOT = os.path.dirname(__file__)  # for detect_pages
logging.getLogger("stbt").setLevel(logging.INFO)


PROMPT_INTRO = """\
You are an agent controlling a GUI application on a set-top-box or TV. You are given:

1. An objective that you are trying to achieve.
2. A simplified text description of the current visible page (more on this below).
3. The valid commands that you can issue from the current page, as Python method signatures.
4. The previous pages you saw and the commands you issued to get to this page (in the order seen/issued, i.e. most recent last).

The description of the current page is in Python syntax: It's the Python repr of a class that models that page of the application. These classes are called "PageObjects". The fully-qualified name of the class shows the app and the type of page; the properties of the class contain information extracted from the page. For example:

    <appletv.Carousel(carousel_name='Top Movies', selected_title='Godzilla vs. Kong')>

Additionally, you can issue the following commands from any page:

1. press("key_name"), where key_name can be "KEY_DOWN", "KEY_UP", "KEY_RIGHT", "KEY_LEFT", "KEY_OK", or "KEY_BACK".
2. launch_app("app_name")
3. print(page.property), where "page" is a Python variable that is already set to an instance of the PageObject for the current visible page, and "property" is the name of a property of that PageObject.
4. assert page.property == some_value

Based on your given objective, issue whatever command you believe will get you closest to achieving your goal.

Your inputs follow. Reply with your next command.

"""


PROMPT_TEMPLATE = """\
OBJECTIVE: {objective}
CURRENT PAGE: {page}
COMMANDS:
{commands}
HISTORY:
{previous_commands}
YOUR COMMAND:"""


verbose = False
interactive = True


def run_test():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Print the prompt we give to GPT-3")
    parser.add_argument(
        "--no-interactive", action="store_false", dest="interactive",
        help="Give GPT-3 free rein (don't prompt for confirmation)")
    args = parser.parse_args(sys.argv[1:])
    global verbose, interactive
    verbose = args.verbose
    interactive = args.interactive

    previous_commands = []
    page = next(stbt.detect_pages(), None)
    assert page, "Failed to detect current page"
    prev_page = None
    error = None
    objective = input_objective()
    while True:
        gpt_command = get_gpt_command(objective, page, previous_commands)
        # Only run the first line
        gpt_command = gpt_command.strip().split("\n")[0].strip()
        print(f"GPT-3 COMMAND: {bold(gpt_command)}")
        if interactive:
            command = input(
                "Enter python code to run "
                "(or press return to run GPT-3's command above):\n").strip()
        else:
            command = None
        if not command:
            command = gpt_command
        time.sleep(1)
        prev_page = page

        try:
            ret = None
            ret = exec(command)  # pylint:disable=exec-used
            error = None
        except (SyntaxError, NameError, AttributeError, FileNotFoundError) as e:
            print(f"{type(e).__name__}: {e}")
            page = None
            error = e

        if isinstance(ret, stbt.FrameObject) and ret.is_visible:
            page = ret
        else:
            page = stbt.wait_until(
                lambda: next(stbt.detect_pages(), None),
                timeout_secs=3)
            assert page, "Failed to detect current page"

        if error:
            previous_commands.append((describe_page(prev_page), command,
                                      type(error).__name__))
        else:
            previous_commands.append((describe_page(prev_page), command,
                                      describe_page(page)))


def get_gpt_command(objective, page, previous_commands):
    page_description = describe_page(page)
    commands = "\n".join(
        f"    page.{name}{inspect.signature(f)}"
        for name, f in inspect.getmembers(page, inspect.ismethod)
        if not name.startswith("_"))

    prompt = PROMPT_TEMPLATE.format(
        page=page_description,
        commands=commands,
        objective=objective,
        previous_commands="\n".join(f"    {a} : {b}"
                                    for a, b, c in previous_commands))
    if verbose:
        debug("")
        debug("=========================================================")
        debug(prompt)
        debug("=========================================================")
    else:
        print(f"CURRENT PAGE: {page_description}")

    with timeit("openai api"):
        response = openai.Completion.create(
            model="text-davinci-002",
            prompt = PROMPT_INTRO + prompt,
            temperature=0.5,
            frequency_penalty=1,
            max_tokens=50)
    # debug(response)

    return response.choices[0].text


def describe_page(page):
    for prop in page._fields:
        # Evaluate each property so that it shows in the repr (otherwise the
        # repr prints "..." because our PageObject properties are lazy).
        getattr(page, prop)
    s = repr(page)
    s = re.sub(r"is_visible=True(, )?", "", s)
    s = re.sub(r"_frame=<([^>]+)>(, )?", "", s)
    if m := re.search(r"^tests\.([a-z]+)\.pages", type(page).__module__):
        app_name = m.group(1)
    else:
        app_name = "unknown"
    return f"<{app_name}.{s[1:]}"


def input_objective(previous_objective=None):
    if previous_objective and not interactive:
        return previous_objective
    if previous_objective:
        message = "Objective (or press return to use previous objective): "
    else:
        message = "Objective: "
    objective = input(message)
    if not objective:
        objective = previous_objective
    assert objective
    return objective


def launch_app(name):
    # Accept different capitalization, with & without spaces.
    name = {
        "btsport": "BT Sport",
        "youtube": "YouTube",
    }.get(name.lower().replace(" ", ""),
          name)
    Home.launch_app(name)


def press(key_name):
    stbt.press_and_wait(key_name)  # pylint:disable=stbt-unused-return-value


def debug(obj):
    if not verbose:
        return
    if isinstance(obj, str):
        print(obj)
    else:
        pprint(obj)


def bold(s):
    return "\033[1;1m%s\033[0m" % (s,)


@contextmanager
def timeit(description):
    start = time.time()
    try:
        yield
    finally:
        debug(f"{description} took {time.time() - start:.2f}s")
