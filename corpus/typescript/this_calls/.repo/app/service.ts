export class Service {
  helper(): string {
    return "s";
  }

  run(): string {
    return this.helper();
  }
}
