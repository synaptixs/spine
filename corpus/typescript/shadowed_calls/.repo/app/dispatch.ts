export function handle(): number {
  return 1;
}

export function run(handle: () => number): number {
  return handle();
}

export function direct(): number {
  return handle();
}
