import { useMemo, useState } from "react";
import { DayPicker, TZDate } from "@daypicker/react";
import * as Popover from "@radix-ui/react-popover";
import { exchangeDate, exchangeZone } from "./exchange";
import "@daypicker/react/style.css";

function calendarDate(value: string): Date {
  const [year, month, day] = value.split("-").map(Number);
  return new TZDate(year, month - 1, day, 12, 0, 0, exchangeZone);
}

export function CollectionDatePicker({
  dates,
  value,
  onChange,
}: {
  dates: string[];
  value: string;
  onChange: (date: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const available = useMemo(() => new Set(dates), [dates]);
  const ordered = useMemo(() => [...dates].sort(), [dates]);
  const selected = value ? calendarDate(value) : undefined;

  return (
    <div className="date-control">
      <label id="date-label" htmlFor="date">
        Collection date · Eastern Time
      </label>
      <Popover.Root open={open} onOpenChange={setOpen}>
        <Popover.Trigger
          id="date"
          className="date-trigger"
          aria-labelledby="date-label date-value"
          disabled={!dates.length}
        >
          <span id="date-value">{value || "No dates available"}</span>
          <svg
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <rect x="3" y="5" width="18" height="16" rx="2" />
            <path d="M16 3v4M8 3v4M3 11h18" />
          </svg>
        </Popover.Trigger>
        <Popover.Portal>
          <Popover.Content
            className="collection-date-popover"
            align="end"
            sideOffset={8}
            collisionPadding={8}
            aria-label="Choose collection date"
            aria-describedby="date-availability"
            onOpenAutoFocus={(event) => event.preventDefault()}
          >
            <DayPicker
              mode="single"
              required
              autoFocus
              timeZone={exchangeZone}
              selected={selected}
              defaultMonth={selected}
              startMonth={ordered[0] ? calendarDate(ordered[0]) : undefined}
              endMonth={
                ordered.at(-1) ? calendarDate(ordered.at(-1)!) : undefined
              }
              captionLayout="dropdown"
              navLayout="around"
              disabled={(day) => !available.has(exchangeDate(day.getTime()))}
              onSelect={(day) => {
                const next = exchangeDate(day.getTime());
                if (!available.has(next)) return;
                if (next !== value) onChange(next);
                setOpen(false);
              }}
            />
            <p id="date-availability" className="date-availability">
              Only available collection dates can be selected.
            </p>
          </Popover.Content>
        </Popover.Portal>
      </Popover.Root>
      <span>Recent replay · lasting summaries</span>
    </div>
  );
}
