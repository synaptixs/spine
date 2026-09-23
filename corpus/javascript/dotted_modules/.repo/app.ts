import { User, find } from './models/user.model';

export class Admin extends User {}

export function run(): boolean {
  find();
  return new User().save();
}
