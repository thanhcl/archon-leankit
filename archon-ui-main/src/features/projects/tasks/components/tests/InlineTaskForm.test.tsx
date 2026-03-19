import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { InlineTaskForm } from "../InlineTaskForm";

describe("InlineTaskForm", () => {
  const defaultProps = {
    projectId: "proj-123",
    status: "todo" as const,
    onSubmit: vi.fn(),
    onCancel: vi.fn(),
    isSubmitting: false,
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders title input and priority dropdown", () => {
    render(<InlineTaskForm {...defaultProps} />);
    expect(screen.getByPlaceholderText("Task title...")).toBeInTheDocument();
    expect(screen.getByRole("combobox")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /add/i })).toBeInTheDocument();
  });

  it("focuses input on mount", () => {
    render(<InlineTaskForm {...defaultProps} />);
    expect(screen.getByPlaceholderText("Task title...")).toHaveFocus();
  });

  it("submits on Enter with title and priority", async () => {
    const user = userEvent.setup();
    render(<InlineTaskForm {...defaultProps} />);

    const input = screen.getByPlaceholderText("Task title...");
    await user.type(input, "New task{Enter}");

    expect(defaultProps.onSubmit).toHaveBeenCalledWith({
      title: "New task",
      priority: "medium",
      status: "todo",
    });
  });

  it("submits on Add button click", async () => {
    const user = userEvent.setup();
    render(<InlineTaskForm {...defaultProps} />);

    await user.type(screen.getByPlaceholderText("Task title..."), "Button task");
    await user.click(screen.getByRole("button", { name: /add/i }));

    expect(defaultProps.onSubmit).toHaveBeenCalledWith({
      title: "Button task",
      priority: "medium",
      status: "todo",
    });
  });

  it("does not submit with empty title", async () => {
    const user = userEvent.setup();
    render(<InlineTaskForm {...defaultProps} />);

    await user.keyboard("{Enter}");
    expect(defaultProps.onSubmit).not.toHaveBeenCalled();
  });

  it("calls onCancel on Escape", async () => {
    const user = userEvent.setup();
    render(<InlineTaskForm {...defaultProps} />);

    await user.keyboard("{Escape}");
    expect(defaultProps.onCancel).toHaveBeenCalled();
  });

  it("calls onCancel on click outside", () => {
    render(
      <div>
        <div data-testid="outside">Outside</div>
        <InlineTaskForm {...defaultProps} />
      </div>,
    );

    fireEvent.mouseDown(screen.getByTestId("outside"));
    expect(defaultProps.onCancel).toHaveBeenCalled();
  });

  it("allows changing priority before submit", async () => {
    const user = userEvent.setup();
    render(<InlineTaskForm {...defaultProps} />);

    await user.selectOptions(screen.getByRole("combobox"), "high");
    await user.type(screen.getByPlaceholderText("Task title..."), "High prio task{Enter}");

    expect(defaultProps.onSubmit).toHaveBeenCalledWith({
      title: "High prio task",
      priority: "high",
      status: "todo",
    });
  });

  it("disables inputs when submitting", () => {
    render(<InlineTaskForm {...defaultProps} isSubmitting={true} />);

    expect(screen.getByPlaceholderText("Task title...")).toBeDisabled();
    expect(screen.getByRole("combobox")).toBeDisabled();
  });

  it("clears input after successful submit", async () => {
    const user = userEvent.setup();
    render(<InlineTaskForm {...defaultProps} />);

    const input = screen.getByPlaceholderText("Task title...");
    await user.type(input, "New task{Enter}");

    expect(input).toHaveValue("");
  });

  it("uses column status for submission", async () => {
    const user = userEvent.setup();
    render(<InlineTaskForm {...defaultProps} status="doing" />);

    await user.type(screen.getByPlaceholderText("Task title..."), "Doing task{Enter}");

    expect(defaultProps.onSubmit).toHaveBeenCalledWith(expect.objectContaining({ status: "doing" }));
  });
});
