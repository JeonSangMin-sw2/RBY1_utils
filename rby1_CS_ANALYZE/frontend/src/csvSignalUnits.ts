export type CsvSignalUnit = {
  axisLabel: string;
  symbol: string;
  scale: number;
};

const RAD_TO_DEG = 180 / Math.PI;

const CSV_SIGNAL_UNITS: Readonly<Record<string, CsvSignalUnit>> = {
  position: { axisLabel: "위치 (deg)", symbol: "deg", scale: RAD_TO_DEG },
  velocity: { axisLabel: "속도 (deg/s)", symbol: "deg/s", scale: RAD_TO_DEG },
  current: { axisLabel: "전류 (A)", symbol: "A", scale: 1 },
  torque: { axisLabel: "토크 (Nm)", symbol: "Nm", scale: 1 },
  temperature: { axisLabel: "온도 (°C)", symbol: "°C", scale: 1 },
};

export function csvSignalUnit(category: string): CsvSignalUnit | undefined {
  return CSV_SIGNAL_UNITS[category];
}

export function csvSignalDisplayValue(category: string, value: number): number {
  return value * (csvSignalUnit(category)?.scale ?? 1);
}
