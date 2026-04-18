const EXACT_SERIES_PACK_PATTERNS = [
  [6],
  [3, 3],
  [2, 4],
  [2, 2, 2],
];

export function packSeriesRailRows(seriesRails) {
  const remaining = [...(seriesRails || [])];
  const packedRows = [];

  function takeExactPattern(pattern) {
    const matchedRails = [];
    const matchedIndices = [];
    for (const size of pattern) {
      const nextIndex = remaining.findIndex(
        (rail, index) => !matchedIndices.includes(index) && Number(rail?.film_count || 0) === size,
      );
      if (nextIndex === -1) {
        return null;
      }
      matchedIndices.push(nextIndex);
      matchedRails.push(remaining[nextIndex]);
    }
    matchedIndices
      .sort((left, right) => right - left)
      .forEach((index) => {
        remaining.splice(index, 1);
      });
    return {
      key: matchedRails.map((rail) => rail.key).join("__"),
      blocks: matchedRails.map((rail, blockIndex) => ({
        key: `${rail.key}-${blockIndex}`,
        rail,
        slots: Number(rail.film_count || 0),
      })),
    };
  }

  for (const pattern of EXACT_SERIES_PACK_PATTERNS) {
    while (true) {
      const nextRow = takeExactPattern(pattern);
      if (!nextRow) {
        break;
      }
      packedRows.push(nextRow);
    }
  }

  const largeSeries = remaining.filter((rail) => Number(rail?.film_count || 0) > 6);
  const smallSeries = remaining.filter((rail) => Number(rail?.film_count || 0) <= 6);

  largeSeries.forEach((rail) => {
    packedRows.push({
      key: rail.key,
      blocks: [{ key: rail.key, rail, slots: 6 }],
    });
  });

  smallSeries.forEach((rail) => {
    packedRows.push({
      key: rail.key,
      blocks: [{ key: rail.key, rail, slots: 6 }],
    });
  });

  return packedRows;
}
