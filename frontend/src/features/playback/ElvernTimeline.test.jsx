import { render } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import ElvernTimeline from "./ElvernTimeline.jsx";

describe("ElvernTimeline", () => {
  test("renders progress without a standalone playhead knob", () => {
    const { container } = render(
      <ElvernTimeline
        bufferedAbsoluteRanges={[[0, 80]]}
        currentTimeSeconds={40}
        durationSeconds={100}
        onSeekCommit={() => {}}
      />,
    );

    expect(container.querySelector(".elvern-timeline__layer--progress")).not.toBeNull();
    expect(container.querySelector(".elvern-timeline__playhead")).toBeNull();
  });
});
