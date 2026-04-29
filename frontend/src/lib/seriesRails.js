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

function createSeriesRailBlock(rail, blockIndex = 0, slots = 6) {
  return {
    key: blockIndex > 0 ? `${rail.key}-${blockIndex}` : rail.key,
    rail,
    slots,
  };
}

function getVisibleSeriesRailMovieCount(rail) {
  if (Array.isArray(rail?.items)) {
    return rail.items.length;
  }
  return Number(rail?.film_count || 0);
}

function getIpadPortraitSlotCount(rail) {
  const filmCount = getVisibleSeriesRailMovieCount(rail);
  if (filmCount === 2 || filmCount === 3) {
    return filmCount;
  }
  return 6;
}

export function packIpadPortraitSeriesRailRows(seriesRails) {
  const rails = seriesRails || [];
  const pairByFirstIndex = new Map();
  const pairedSecondIndexes = new Set();
  const twoFilmEntries = rails
    .map((rail, index) => ({ rail, index }))
    .filter(({ rail }) => getVisibleSeriesRailMovieCount(rail) === 2);

  for (let index = 0; index + 1 < twoFilmEntries.length; index += 2) {
    const first = twoFilmEntries[index];
    const second = twoFilmEntries[index + 1];
    pairByFirstIndex.set(first.index, {
      key: `${first.rail.key}__${second.rail.key}`,
      layout: "ipad-two-pair",
      blocks: [
        createSeriesRailBlock(first.rail, 0, 2),
        createSeriesRailBlock(second.rail, 1, 2),
      ],
    });
    pairedSecondIndexes.add(second.index);
  }

  return rails.flatMap((rail, index) => {
    if (pairedSecondIndexes.has(index)) {
      return [];
    }
    const pairedRow = pairByFirstIndex.get(index);
    if (pairedRow) {
      return [pairedRow];
    }
    return [{
      key: rail.key,
      blocks: [createSeriesRailBlock(rail, 0, getIpadPortraitSlotCount(rail))],
    }];
  });
}
