import hashlib
from typing import Any

UTILITY_EXPRESSION_SHA256_PREFIX = "6947ff8ef8a7"
UTILITY_CALL = "(utilityScript, ...args) => utilityScript.evaluate(...args)"
CONTENT_EXPRESSION = """() => {
        let retVal = "";
        if (document.doctype)
          retVal = new XMLSerializer().serializeToString(document.doctype);
        if (document.documentElement)
          retVal += document.documentElement.outerHTML;
        return retVal;
      }"""


def is_utility_evaluation(params: dict[str, Any]) -> bool:
    expression = params.get("expression")
    return (
        isinstance(expression, str)
        and hashlib.sha256(expression.encode())
        .hexdigest()
        .startswith(UTILITY_EXPRESSION_SHA256_PREFIX)
    )


def is_content_call(
    params: dict[str, Any],
    *,
    utility_objects: set[str] | None = None,
) -> bool:
    object_id = params.get("objectId")
    arguments = params.get("arguments")
    if not isinstance(object_id, str) or not isinstance(arguments, list):
        return False
    if utility_objects is not None and object_id not in utility_objects:
        return False
    if params.get("functionDeclaration") != UTILITY_CALL or len(arguments) != 6:
        return False
    return (
        arguments[0] == {"objectId": object_id}
        and arguments[1] == {"value": True}
        and arguments[2] == {"value": True}
        and arguments[3] == {"value": CONTENT_EXPRESSION}
        and arguments[4] == {"value": 1}
        and arguments[5] == {"value": {"v": "undefined"}}
        and params.get("returnByValue") is True
        and params.get("awaitPromise") is True
    )
