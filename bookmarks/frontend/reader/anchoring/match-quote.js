/**
 * Text quote matching algorithm.
 *
 * Adapted from Hypothesis client (https://github.com/hypothesis/client),
 * file: src/annotator/anchoring/match-quote.ts
 * Licensed under the 2-Clause BSD License.
 *
 * Finds the best approximate match for a quote within a document's text,
 * using edit-distance based fuzzy matching with context-aware scoring.
 */

import approxSearch from "approx-string-match";

/**
 * Find the best approximate matches for `str` in `text` allowing up to
 * `maxErrors` errors.
 *
 * @param {string} text
 * @param {string} str
 * @param {number} maxErrors
 * @returns {Array<{start: number, end: number, errors: number}>}
 */
function search(text, str, maxErrors) {
  // Do a fast search for exact matches. The `approx-string-match` library
  // doesn't currently incorporate this optimization itself.
  let matchPos = 0;
  const exactMatches = [];
  while (matchPos !== -1) {
    matchPos = text.indexOf(str, matchPos);
    if (matchPos !== -1) {
      exactMatches.push({
        start: matchPos,
        end: matchPos + str.length,
        errors: 0,
      });
      matchPos += 1;
    }
  }
  if (exactMatches.length > 0) {
    return exactMatches;
  }

  // If there are no exact matches, do a more expensive search for matches
  // with errors.
  return approxSearch(text, str, maxErrors);
}

/**
 * Compute a score between 0 and 1.0 for the similarity between `text` and `str`.
 *
 * @param {string} text
 * @param {string} str
 * @returns {number}
 */
function textMatchScore(text, str) {
  if (str.length === 0 || text.length === 0) {
    return 0.0;
  }

  const matches = search(text, str, str.length);
  return 1 - matches[0].errors / str.length;
}

/**
 * Find the best approximate match for `quote` in `text`.
 *
 * @param {string} text - Document text to search
 * @param {string} quote - String to find within `text`
 * @param {{prefix?: string, suffix?: string, hint?: number}} [context={}] -
 *   Context in which the quote originally appeared. Used to choose the best match.
 * @returns {{start: number, end: number, score: number} | null}
 *   Match result or null if no match found.
 */
export function matchQuote(text, quote, context = {}) {
  if (quote.length === 0) {
    return null;
  }

  // Choose the maximum number of errors to allow for the initial search.
  // This choice involves a tradeoff between:
  //
  //  - Recall (proportion of "good" matches found)
  //  - Precision (proportion of matches found which are "good")
  //  - Cost of the initial search and of processing the candidate matches
  //
  // The expected-time complexity of the initial search is
  // `O((maxErrors / 32) * text.length)`. See `approx-string-match` docs.
  const maxErrors = Math.min(256, quote.length / 2);

  // Find the closest matches for `quote` in `text` based on edit distance.
  const matches = search(text, quote, maxErrors);

  if (matches.length === 0) {
    return null;
  }

  /**
   * Compute a score between 0 and 1.0 for a match candidate.
   *
   * @param {{start: number, end: number, errors: number}} match
   * @returns {number}
   */
  const scoreMatch = (match) => {
    const quoteWeight = 50; // Similarity of matched text to quote.
    const prefixWeight = 20; // Similarity of text before matched text to `context.prefix`.
    const suffixWeight = 20; // Similarity of text after matched text to `context.suffix`.
    const posWeight = 2; // Proximity to expected location. Used as a tie-breaker.

    const quoteScore = 1 - match.errors / quote.length;

    const prefixScore = context.prefix
      ? textMatchScore(
          text.slice(
            Math.max(0, match.start - context.prefix.length),
            match.start
          ),
          context.prefix
        )
      : 1.0;
    const suffixScore = context.suffix
      ? textMatchScore(
          text.slice(match.end, match.end + context.suffix.length),
          context.suffix
        )
      : 1.0;

    let posScore = 1.0;
    if (typeof context.hint === "number") {
      const offset = Math.abs(match.start - context.hint);
      posScore = 1.0 - offset / text.length;
    }

    const rawScore =
      quoteWeight * quoteScore +
      prefixWeight * prefixScore +
      suffixWeight * suffixScore +
      posWeight * posScore;
    const maxScore = quoteWeight + prefixWeight + suffixWeight + posWeight;
    const normalizedScore = rawScore / maxScore;

    return normalizedScore;
  };

  // Rank matches based on similarity of actual and expected surrounding text
  // and actual/expected offset in the document text.
  const scoredMatches = matches.map((m) => ({
    start: m.start,
    end: m.end,
    score: scoreMatch(m),
  }));

  // Choose match with the highest score.
  scoredMatches.sort((a, b) => b.score - a.score);
  return scoredMatches[0];
}
