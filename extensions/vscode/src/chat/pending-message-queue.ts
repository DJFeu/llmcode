export class PendingMessageQueue {
  private readonly messages: string[] = [];

  get size(): number {
    return this.messages.length;
  }

  enqueue(text: string): void {
    const value = text.trim();
    if (!value) {
      return;
    }
    this.messages.push(value);
  }

  drain(): string[] {
    return this.messages.splice(0, this.messages.length);
  }
}
