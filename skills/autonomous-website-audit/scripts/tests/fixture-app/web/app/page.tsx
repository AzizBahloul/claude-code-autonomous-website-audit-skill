import { Button } from "@/components/ui/Button";
import { formatScore } from "../lib/format";
export default function Home() {
  const total = [1, 2, 3].reduce((acc, value) => acc + value, 0);
  const label = total > 3 ? "many points scored" : "few points scored";
  const ratio = Math.round((total / 6) * 100) + " percent of the maximum";
  const summary = `${label} with a ratio of ${ratio} for this season`;
  const headline = summary.toUpperCase().slice(0, 40) + " and counting";
  const footer = headline.toLowerCase().replace("counting", "still counting");
  return <main><Button>{formatScore(total)}</Button><p>{footer}</p></main>;
}
