/*
 * Copyright (c) 2026, MariaDB plc.
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License, version 2.0,
 * as published by the Free Software Foundation.
 *
 * This program is distributed in the hope that it will be useful, but
 * WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
 * the GNU General Public License, version 2.0, for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the Free Software Foundation, Inc.,
 * 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA
 */

import { describe, expect, it } from "vitest";

import {
    findUpdatableTarget,
    stripLiteralsAndComments,
} from "../../sql/statementTarget.js";

describe("stripLiteralsAndComments", () => {
    it("empties string literals", () => {
        expect(stripLiteralsAndComments("SELECT 'from x' FROM t"))
            .toBe("SELECT '' FROM t");
    });

    it("removes line and block comments", () => {
        expect(stripLiteralsAndComments("SELECT 1 -- join me\nFROM t"))
            .toBe("SELECT 1  \nFROM t");
        expect(stripLiteralsAndComments("SELECT /* join */ 1 FROM t"))
            .toBe("SELECT   1 FROM t");
    });
});

describe("findUpdatableTarget", () => {
    it.each([
        ["SELECT * FROM city", { table: "city" }],
        ["select id, name from world.city", {
            schema: "world",
            table: "city",
        }],
        ["SELECT * FROM `world`.`city`", {
            schema: "world",
            table: "city",
        }],
        ["SELECT * FROM city WHERE id = 1", { table: "city" }],
        ["SELECT * FROM city ORDER BY name LIMIT 10", { table: "city" }],
        ["SELECT * FROM city;", { table: "city" }],
        ["SELECT * FROM city FOR UPDATE", { table: "city" }],
    ] as const)("accepts %s", (sql, expected) => {
        expect(findUpdatableTarget(sql)).toEqual(expected);
    });

    it.each([
        ["SELECT * FROM a JOIN b ON a.id = b.id"],
        ["SELECT * FROM a, b"],
        ["SELECT * FROM a UNION SELECT * FROM b"],
        ["SELECT count(*) FROM city GROUP BY country"],
        ["SELECT DISTINCT name FROM city"],
        ["SELECT * FROM (SELECT 1) AS t"],
        ["SELECT * FROM city WHERE id IN (SELECT id FROM other)"],
        ["UPDATE city SET name = 'x'"],
        ["INSERT INTO city (name) VALUES ('x')"],
        ["CREATE TABLE t (id INT)"],
        ["SHOW TABLES"],
        ["SELECT 1"],
        ["SELECT count(*) FROM city"],
        ["SELECT COUNT(*) AS n FROM world.city"],
        ["SELECT max(Population) FROM city"],
        ["SELECT group_concat(Name) FROM city"],
    ])("rejects %s", (sql) => {
        expect(findUpdatableTarget(sql)).toBeUndefined();
    });

    it("is not fooled by a keyword inside a string", () => {
        expect(findUpdatableTarget("SELECT 'join' FROM city"))
            .toEqual({ table: "city" });
    });

    it("is not fooled by a keyword inside a comment", () => {
        expect(findUpdatableTarget("SELECT 1 /* group by */ FROM city"))
            .toEqual({ table: "city" });
    });

    it("allows an aggregate's name to appear as a column", () => {
        // `counted` is a column, not a call - only a call collapses rows.
        expect(findUpdatableTarget("SELECT counted FROM city"))
            .toEqual({ table: "city" });
    });

    it("allows an aggregate-looking name in the WHERE clause", () => {
        expect(findUpdatableTarget(
            "SELECT Name FROM city WHERE Population > (max_allowed)"))
            .toEqual({ table: "city" });
    });

    it("rejects an aliased table, which no longer names its columns", () => {
        // `FROM city c` leaves a second bare word after the table, which
        // the single table pattern deliberately does not allow.
        expect(findUpdatableTarget("SELECT * FROM city c")).toBeUndefined();
    });
});
