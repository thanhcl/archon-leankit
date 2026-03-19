import { z } from "zod";

// Base validation schemas
export const DatabaseTaskStatusSchema = z.enum([
  "draft",
  "proposed",
  "approved",
  "planning",
  "owner-qa",
  "assigned",
  "executing",
  "architect-review",
  "review",
  "done",
  "failed",
  "escalated",
  "on-hold",
  "cancelled",
]);
export const TaskPrioritySchema = z.enum(["low", "medium", "high", "critical"]);
export const TaskComplexitySchema = z.enum(["simple", "complex"]);

// Assignee schema - flexible string for any agent name
export const AssigneeSchema = z
  .string()
  .min(1, "Assignee cannot be empty")
  .max(100, "Assignee name must be less than 100 characters");

// Task schemas
export const CreateTaskSchema = z.object({
  project_id: z.string().uuid("Project ID must be a valid UUID"),
  parent_task_id: z.string().uuid("Parent task ID must be a valid UUID").optional(),
  title: z.string().min(1, "Task title is required").max(255, "Task title must be less than 255 characters"),
  description: z.string().max(10000, "Task description must be less than 10000 characters").default(""),
  status: DatabaseTaskStatusSchema.default("draft"),
  assignee: AssigneeSchema.default("User"),
  task_order: z.number().int().min(0).default(0),
  feature: z.string().max(100, "Feature name must be less than 100 characters").optional(),
  featureColor: z
    .string()
    .regex(/^#[0-9A-F]{6}$/i, "Feature color must be a valid hex color")
    .optional(),
  priority: TaskPrioritySchema.default("medium"),
  sources: z.array(z.any()).default([]),
  code_examples: z.array(z.any()).default([]),
  // Lifecycle fields
  blocked_by: z.array(z.string().uuid("Blocker task ID must be a valid UUID")).optional(),
  owner: z.string().optional(),
  acceptance_criteria: z.array(z.unknown()).optional(),
  execution_prompt: z.string().max(50000, "Execution prompt must be less than 50000 characters").optional(),
  source_app: z.string().optional(),
  complexity: TaskComplexitySchema.default("simple").optional(),
  max_retries: z.number().int().min(0).max(10).default(3).optional(),
});

export const UpdateTaskSchema = CreateTaskSchema.partial().omit({
  project_id: true,
});

export const TaskSchema = z.object({
  id: z.string().uuid("Task ID must be a valid UUID"),
  project_id: z.string().uuid("Project ID must be a valid UUID"),
  parent_task_id: z.string().uuid().optional(),
  title: z.string().min(1),
  description: z.string(),
  status: DatabaseTaskStatusSchema,
  assignee: AssigneeSchema,
  task_order: z.number().int().min(0),
  sources: z.array(z.any()).default([]),
  code_examples: z.array(z.any()).default([]),
  created_at: z.string().datetime(),
  updated_at: z.string().datetime(),

  // Extended UI properties
  feature: z.string().optional(),
  featureColor: z.string().optional(),
  priority: TaskPrioritySchema.optional(),

  // Lifecycle fields
  blocked_by: z.array(z.string().uuid()).optional(),
  complexity: TaskComplexitySchema.optional(),
  owner: z.string().optional(),
  source_app: z.string().optional(),
  retry_count: z.number().optional(),
  max_retries: z.number().optional(),
  state_changed_at: z.string().optional(),

  // Large fields (may be absent when exclude_large_fields=true)
  acceptance_criteria: z.array(z.unknown()).optional(),
  execution_result: z.record(z.unknown()).nullable().optional(),
  architect_review: z.record(z.unknown()).nullable().optional(),
  execution_prompt: z.string().optional(),
  state_history: z.array(z.record(z.unknown())).optional(),

  // Stats (present when exclude_large_fields=true)
  stats: z
    .object({
      sources_count: z.number(),
      code_examples_count: z.number(),
    })
    .optional(),

  // Soft delete fields
  archived: z.boolean().optional(),
  archived_at: z.string().optional(),
  archived_by: z.string().optional(),
});

// Update task status schema (for drag & drop operations)
export const UpdateTaskStatusSchema = z.object({
  task_id: z.string().uuid("Task ID must be a valid UUID"),
  status: DatabaseTaskStatusSchema,
});

// Validation helper functions
export function validateTask(data: unknown) {
  return TaskSchema.safeParse(data);
}

export function validateCreateTask(data: unknown) {
  return CreateTaskSchema.safeParse(data);
}

export function validateUpdateTask(data: unknown) {
  return UpdateTaskSchema.safeParse(data);
}

export function validateUpdateTaskStatus(data: unknown) {
  return UpdateTaskStatusSchema.safeParse(data);
}

// Export type inference helpers
export type CreateTaskInput = z.infer<typeof CreateTaskSchema>;
export type UpdateTaskInput = z.infer<typeof UpdateTaskSchema>;
export type UpdateTaskStatusInput = z.infer<typeof UpdateTaskStatusSchema>;
export type TaskInput = z.infer<typeof TaskSchema>;
