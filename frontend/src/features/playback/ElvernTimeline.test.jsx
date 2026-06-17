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

  test("renders a continuous server prepared layer from playhead to prepared frontier", () => {
    const { container } = render(
      <ElvernTimeline
        bufferedAbsoluteRanges={[[0, 40], [70, 80]]}
        currentTimeSeconds={40}
        durationSeconds={100}
        onSeekCommit={() => {}}
        serverPreparedThroughSeconds={80}
      />,
    );

    const serverPrepared = container.querySelector(".elvern-timeline__layer--server-prepared");

    expect(serverPrepared).not.toBeNull();
    expect(serverPrepared).toHaveStyle({ left: "40%", width: "40%" });
    expect(container.querySelectorAll(".elvern-timeline__layer--buffered")).toHaveLength(2);
  });

  test("does not render server prepared width when the frontier matches the playhead", () => {
    const { container } = render(
      <ElvernTimeline
        bufferedAbsoluteRanges={[[0, 60]]}
        currentTimeSeconds={40}
        durationSeconds={100}
        onSeekCommit={() => {}}
        serverPreparedThroughSeconds={40}
      />,
    );

    expect(container.querySelector(".elvern-timeline__layer--server-prepared")).toBeNull();
  });

  test("renders preparing target marker on the timeline track when a target is known", () => {
    const { container } = render(
      <ElvernTimeline
        bufferedAbsoluteRanges={[[0, 80]]}
        currentTimeSeconds={20}
        durationSeconds={100}
        onSeekCommit={() => {}}
        preparingTargetSeconds={60}
      />,
    );

    const track = container.querySelector(".elvern-timeline__track");
    const marker = container.querySelector(".elvern-timeline__preparing-marker");

    expect(marker).not.toBeNull();
    expect(track).toContainElement(marker);
    expect(marker).toHaveClass("elvern-timeline__preparing-marker--target");
    expect(marker).toHaveStyle({ left: "60%" });
  });

  test("does not render a preparing marker without a valid duration", () => {
    const { container } = render(
      <ElvernTimeline
        bufferedAbsoluteRanges={[]}
        currentTimeSeconds={0}
        durationSeconds={0}
        onSeekCommit={() => {}}
        preparingTargetSeconds={60}
      />,
    );

    expect(container.querySelector(".elvern-timeline__preparing-marker")).toBeNull();
  });
});
