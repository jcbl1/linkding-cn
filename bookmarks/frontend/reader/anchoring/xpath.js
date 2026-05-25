/**
 * Simple XPath generation and resolution utilities.
 *
 * Adapted from Hypothesis client (https://github.com/hypothesis/client),
 * file: src/annotator/anchoring/xpath.ts
 * Licensed under the 2-Clause BSD License.
 */

/**
 * Get the node name for use in generating an xpath expression.
 *
 * @param {Node} node
 * @returns {string}
 */
function getNodeName(node) {
  const nodeName = node.nodeName.toLowerCase();
  return nodeName === "#text" ? "text()" : nodeName;
}

/**
 * Get the index of the node as it appears in its parent's child list.
 *
 * @param {Node} node
 * @returns {number}
 */
function getNodePosition(node) {
  let pos = 0;
  let tmp = node;
  while (tmp) {
    if (tmp.nodeName === node.nodeName) {
      pos += 1;
    }
    tmp = tmp.previousSibling;
  }
  return pos;
}

/**
 * @param {Node} node
 * @returns {string}
 */
function getPathSegment(node) {
  const name = getNodeName(node);
  const pos = getNodePosition(node);
  return `${name}[${pos}]`;
}

/**
 * A simple XPath generator which can generate XPaths of the form
 * /tag[index]/tag[index].
 *
 * @param {Node} node - The node to generate a path to
 * @param {Node} root - Root node to which the returned path is relative
 * @returns {string}
 */
export function xpathFromNode(node, root) {
  let xpath = "";

  let elem = node;
  while (elem !== root) {
    if (!elem) {
      throw new Error("Node is not a descendant of root");
    }
    xpath = getPathSegment(elem) + "/" + xpath;
    elem = elem.parentNode;
  }
  xpath = "/" + xpath;
  xpath = xpath.replace(/\/$/, ""); // Remove trailing slash

  return xpath;
}

/**
 * Return the `index`'th immediate child of `element` whose tag name is
 * `nodeName` (case insensitive).
 *
 * @param {Element} element
 * @param {string} nodeName
 * @param {number} index
 * @returns {Element | null}
 */
function nthChildOfType(element, nodeName, index) {
  nodeName = nodeName.toUpperCase();

  let matchIndex = -1;
  for (let i = 0; i < element.children.length; i++) {
    const child = element.children[i];
    if (child.nodeName.toUpperCase() === nodeName) {
      ++matchIndex;
      if (matchIndex === index) {
        return child;
      }
    }
  }

  return null;
}

/**
 * Evaluate a _simple XPath_ relative to a `root` element and return the
 * matching element.
 *
 * A _simple XPath_ is a sequence of one or more `/tagName[index]` strings.
 *
 * @param {string} xpath
 * @param {Element} root
 * @returns {Node | null}
 */
function evaluateSimpleXPath(xpath, root) {
  const isSimpleXPath =
    xpath.match(/^(\/[A-Za-z0-9-]+(\[[0-9]+\])?)+$/) !== null;
  if (!isSimpleXPath) {
    throw new Error("Expression is not a simple XPath");
  }

  const segments = xpath.split("/");
  let element = root;

  // Remove leading empty segment.
  segments.shift();

  for (const segment of segments) {
    let elementName;
    let elementIndex;

    const separatorPos = segment.indexOf("[");
    if (separatorPos !== -1) {
      elementName = segment.slice(0, separatorPos);

      const indexStr = segment.slice(separatorPos + 1, segment.indexOf("]"));
      elementIndex = parseInt(indexStr) - 1;
      if (elementIndex < 0) {
        return null;
      }
    } else {
      elementName = segment;
      elementIndex = 0;
    }

    const child = nthChildOfType(element, elementName, elementIndex);
    if (!child) {
      return null;
    }

    element = child;
  }

  return element;
}

/**
 * Finds an element node using an XPath relative to `root`.
 *
 * @param {string} xpath
 * @param {Element} [root=document.body]
 * @returns {Node | null}
 */
export function nodeFromXPath(xpath, root = document.body) {
  try {
    return evaluateSimpleXPath(xpath, root);
  } catch {
    return document.evaluate(
      "." + xpath,
      root,
      null,
      XPathResult.FIRST_ORDERED_NODE_TYPE,
      null
    ).singleNodeValue;
  }
}
