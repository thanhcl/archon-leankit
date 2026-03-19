import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import React from "react";
import { Card, type CardProps } from "./card";

interface DraggableCardProps extends Omit<CardProps, "ref"> {
  itemType: string;
  itemId: string;
  index: number;
  onDrop?: (draggedId: string, targetIndex: number) => void;
  isDragging?: boolean;
  onDragStart?: () => void;
  onDragEnd?: () => void;
}

export const DraggableCard = React.forwardRef<HTMLDivElement, DraggableCardProps>(
  ({ itemId, children, className, ...cardProps }, _ref) => {
    const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
      id: itemId,
    });

    const style = {
      transform: CSS.Transform.toString(transform),
      transition,
    };

    return (
      <div
        ref={setNodeRef}
        style={style}
        {...attributes}
        {...listeners}
        className={isDragging ? "opacity-50 scale-95 transition-all" : "transition-all"}
      >
        <Card {...cardProps} className={className}>
          {children}
        </Card>
      </div>
    );
  },
);

DraggableCard.displayName = "DraggableCard";
